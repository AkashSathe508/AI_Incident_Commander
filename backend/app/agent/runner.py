"""
Agora AI Agent Runner — standalone process with Deepgram STT & Spoken AI Summaries.

Joins an Agora channel as a headless participant (UID in reserved range
900_000_001–999_999_999), subscribes to all remote audio streams, streams
per-speaker PCM audio into Deepgram Live STT, persists normalized transcript
segments to PostgreSQL, and periodically synthesizes and speaks spoken voice
status updates into the room via ElevenLabs & Agora outgoing audio track.

Usage (inside Docker container):
    python runner.py --meeting-id <uuid>

Platform: Linux / macOS (agora_python_server_sdk wraps a native .so).
"""

import argparse
import asyncio
import logging
import os
import random
import signal
import sys
import time
import uuid
import wave
import io
from collections import defaultdict
from typing import Any

import httpx

from app.agent.summarizer import generate_spoken_summary
from app.agent.tts import synthesize_speech
from app.ingestion.transcript_ingestor import transcript_ingestor
# New imports for mode/mute handling
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.meeting import Meeting

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agora-agent] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("agora-agent")

# ── Constants ──────────────────────────────────────────────────────────────────
AGENT_UID_MIN = 900_000_001
AGENT_UID_MAX = 999_999_999
TOKEN_EXPIRY_SECONDS = 3600   # 1 hour token lifetime
TOKEN_RENEWAL_AT = 3300       # renew at 55-minute mark (5 min before expiry)
STATS_INTERVAL = 5            # log frame counts every N seconds
DEFAULT_SPOKEN_INTERVAL = 180 # speak summary every 3 minutes


# ── Token helper ───────────────────────────────────────────────────────────────

def _make_token(app_id: str, app_cert: str, channel: str, uid: int) -> str | None:
    """Generate Agora AccessToken2. Returns None when cert is not configured."""
    if not app_cert or not app_id:
        return None
    try:
        from agora_token_builder import RtcTokenBuilder  # type: ignore
        privilege_expired_ts = int(time.time()) + TOKEN_EXPIRY_SECONDS
        return RtcTokenBuilder.buildTokenWithUid(
            app_id, app_cert, channel, uid,
            1,                          # role = Role_Publisher
            privilege_expired_ts,       # absolute Unix timestamp
        )
    except Exception as exc:
        logger.error(f"Token generation failed: {exc}")
        return None


# ── DB lookup ──────────────────────────────────────────────────────────────────

def _get_channel_name(meeting_id: str) -> str:
    """
    Fetch channel_name from PostgreSQL synchronously.
    Uses psycopg2-binary via SQLAlchemy sync engine.
    """
    from sqlalchemy import create_engine, text  # type: ignore

    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT channel_name FROM meetings WHERE id = :id"),
                {"id": meeting_id},
            ).first()
    finally:
        engine.dispose()

    if row is None:
        raise ValueError(f"Meeting {meeting_id} not found in database")
    channel_name = row[0]
    if not channel_name:
        raise ValueError(f"Meeting {meeting_id} has no channel_name set")
    return channel_name


# ── Audio conversion helper ────────────────────────────────────────────────────

def _wav_to_pcm(wav_bytes: bytes) -> bytes:
    """Converts WAV audio data to raw PCM (16kHz, 16-bit, mono)."""
    with wave.open(io.BytesIO(wav_bytes), 'rb') as wav_file:
        return wav_file.readframes(wav_file.getnframes())


# ── Deepgram Stream Manager ────────────────────────────────────────────────────

class DeepgramManager:
    """
    Manages real-time per-speaker Deepgram STT connections.
    """

    def __init__(self, meeting_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self.meeting_id = meeting_id
        self.loop = loop
        self.api_key = os.environ.get("DEEPGRAM_API_KEY", "")
        self.audio_queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
        self.dg_connections: dict[int, Any] = {}
        self.start_time = time.monotonic()
        self._worker_task: asyncio.Task | None = None
        self._httpx_client: httpx.AsyncClient | None = None

        if not self.api_key:
            logger.warning(
                "DEEPGRAM_API_KEY environment variable is not set — "
                "live speech-to-text transcription will be disabled"
            )

    async def start(self) -> None:
        """Start the background audio processing worker."""
        self._httpx_client = httpx.AsyncClient(timeout=5.0)
        self._worker_task = asyncio.create_task(self._process_audio_queue())
        logger.info("Deepgram manager started for meeting %s", self.meeting_id[:8])

    async def stop(self) -> None:
        """Close Deepgram connections and stop worker."""
        if self._worker_task:
            self._worker_task.cancel()
        if self._httpx_client:
            await self._httpx_client.aclose()
        for uid, conn in self.dg_connections.items():
            try:
                await conn.finish()
            except Exception:
                pass
        logger.info("Deepgram manager stopped")

    def enqueue_audio(self, uid: int, pcm_bytes: bytes) -> None:
        """Thread-safe enqueue from Agora callback into asyncio queue."""
        if self.api_key and pcm_bytes:
            self.loop.call_soon_threadsafe(self.audio_queue.put_nowait, (uid, pcm_bytes))

    async def _get_or_create_connection(self, uid: int) -> Any:
        if uid in self.dg_connections:
            return self.dg_connections[uid]

        try:
            from deepgram import (  # type: ignore
                DeepgramClient,
                LiveTranscriptionEvents,
                LiveOptions,
            )
        except ImportError:
            logger.error("deepgram-sdk package not installed")
            return None

        try:
            client = DeepgramClient(self.api_key)
            dg_conn = client.listen.asyncwebsocket.v("1")

            async def _on_transcript(self_dg, result, **kwargs):
                try:
                    sentence = result.channel.alternatives[0].transcript
                    if sentence and sentence.strip():
                        rel_start = getattr(result, "start", 0.0)
                        rel_duration = getattr(result, "duration", 1.0)
                        start_ms = int(rel_start * 1000)
                        end_ms = int((rel_start + rel_duration) * 1000)
                        confidence = result.channel.alternatives[0].confidence

                        payload = transcript_ingestor.process_and_save(
                            meeting_id=self.meeting_id,
                            speaker_id=str(uid),
                            text_content=sentence,
                            start_ms=start_ms,
                            end_ms=end_ms,
                            confidence=confidence,
                            speaker_name=f"Speaker {uid}",
                        )

                        if payload and self._httpx_client:
                            backend_url = os.environ.get(
                                "BACKEND_INTERNAL_URL", "http://127.0.0.1:8000"
                            )
                            url = f"{backend_url}/api/meetings/{self.meeting_id}/broadcast"
                            try:
                                await self._httpx_client.post(url, json=payload)
                            except Exception as exc:
                                logger.warning("Broadcast post failed: %s", exc)
                except Exception as exc:
                    logger.error("Error processing Deepgram transcript callback: %s", exc)

            dg_conn.on(LiveTranscriptionEvents.Transcript, _on_transcript)

            options = LiveOptions(
                model="nova-2",
                language="en-US",
                encoding="linear16",
                sample_rate=16000,
                channels=1,
                interim_results=False,
            )
            await dg_conn.start(options)
            self.dg_connections[uid] = dg_conn
            logger.info("Created Deepgram live stream for speaker UID %d", uid)
            return dg_conn
        except Exception as exc:
            logger.error("Failed to create Deepgram connection for UID %d: %s", uid, exc)
            return None

    async def _process_audio_queue(self) -> None:
        while True:
            try:
                uid, pcm_bytes = await self.audio_queue.get()
                conn = await self._get_or_create_connection(uid)
                if conn:
                    await conn.send(pcm_bytes)
                self.audio_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Error streaming PCM bytes to Deepgram: %s", exc)


# ── Audio frame observer & Speech Guard ───────────────────────────────────────

class FrameCounterObserver:
    """
    Implements the Agora audio frame observer interface.
    Tracks remote speaker frame counts and last human audio timestamp for speech guard.
    """

    def __init__(self, dg_manager: DeepgramManager | None = None):
        self._counts: dict[int, int] = defaultdict(int)
        self.dg_manager = dg_manager
        self.last_human_audio_ts: float = 0.0

    def snapshot(self) -> dict[int, int]:
        return dict(self._counts)

    def on_record_audio_frame(self, audio_frame) -> bool:
        return True

    def on_playback_audio_frame(self, audio_frame) -> bool:
        return True

    def on_mixed_audio_frame(self, audio_frame) -> bool:
        return True

    def on_ear_monitoring_audio_frame(self, audio_frame) -> bool:
        return True

    def on_playback_audio_frame_before_mixing(self, uid, audio_frame) -> bool:
        try:
            n_uid = int(uid)
            self._counts[n_uid] += 1
            self.last_human_audio_ts = time.monotonic()

            if self.dg_manager and hasattr(audio_frame, "buffer"):
                buf = audio_frame.buffer
                if buf:
                    pcm_bytes = bytes(buf)
                    self.dg_manager.enqueue_audio(n_uid, pcm_bytes)
        except Exception:
            pass
        return True


# ── Outgoing Spoken Audio Publisher Helper ────────────────────────────────────

async def play_spoken_summary_into_room(conn: Any, pcm_bytes: bytes) -> None:
    """
    Pushes raw 16kHz PCM audio bytes in 20ms chunks into the Agora channel for out-loud room playback.
    640 bytes = 20ms of 16kHz 16-bit mono PCM.
    """
    chunk_size = 640
    num_chunks = len(pcm_bytes) // chunk_size

    push_fn = getattr(conn, "push_audio_pcm_data", None) or getattr(conn, "send_audio_pcm_data", None)
    if push_fn is None:
        logger.warning("Agora connection object has no push_audio_pcm_data method — skipping playback")
        return

    logger.info("[SPOKEN SUMMARY] Playing %d audio chunks into room...", num_chunks)
    for i in range(num_chunks):
        chunk = pcm_bytes[i * chunk_size : (i + 1) * chunk_size]
        try:
            push_fn(chunk)
        except Exception as exc:
            logger.warning("Failed to push audio PCM chunk to Agora: %s", exc)
        await asyncio.sleep(0.02)  # 20ms chunk cadence


# ── Main agent coroutine ───────────────────────────────────────────────────────

async def run_agent(meeting_id: str) -> None:
    app_id = os.environ.get("AGORA_APP_ID", "")
    app_cert = os.environ.get("AGORA_APP_CERTIFICATE", "")

    if not app_id:
        logger.error("AGORA_APP_ID env var is not set — cannot join channel")
        sys.exit(1)

    # ── Step 1: Resolve meeting channel name ───────────────────────────────────
    logger.info(f"Resolving meeting {meeting_id}...")
    channel_name = _get_channel_name(meeting_id)
    logger.info(f"Channel name: {channel_name}")

    # ── Step 2: Pick a UID in the reserved AI agent range ─────────────────────
    agent_uid = random.randint(AGENT_UID_MIN, AGENT_UID_MAX)
    logger.info(f"Agent UID: {agent_uid} (reserved AI range {AGENT_UID_MIN}–{AGENT_UID_MAX})")

    # ── Step 3: Generate initial join token ────────────────────────────────────
    token = _make_token(app_id, app_cert, channel_name, agent_uid)
    token_ts = time.monotonic()

    # ── Step 4: Setup Deepgram manager ─────────────────────────────────────────
    loop = asyncio.get_running_loop()
    dg_manager = DeepgramManager(meeting_id, loop)
    await dg_manager.start()

    # ── Step 5: Import agora_python_server_sdk ─────────────────────────
    try:
        from agora.rtc.agora_service import (  # type: ignore
            AgoraService,
            AgoraServiceConfig,
            RTCConnConfig,
        )
        from agora.rtc.agora_base import (  # type: ignore
            ClientRoleType,
            ChannelProfileType,
            AudioScenarioType,
            AudioProfileType,
            RtcConnectionPublishConfig,
        )
    except ImportError as exc:
        logger.error(
            f"Cannot import agora_python_server_sdk: {exc}\n"
            "This SDK requires Linux or macOS. "
            "The agent must run inside the Docker container."
        )
        await dg_manager.stop()
        sys.exit(2)

    # ── Step 6: Initialize Agora Service ──────────────────────────────────────
    logger.info("Initializing AgoraService...")
    agora_service = AgoraService()
    svc_cfg = AgoraServiceConfig()
    svc_cfg.appid = app_id
    svc_cfg.enable_audio_processor = True
    svc_cfg.enable_audio_device = False
    svc_cfg.enable_video = False
    agora_service.initialize(svc_cfg)
    logger.info("AgoraService initialized")

    # ── Step 7: Configure and create RTC connection with audio publish enabled ─────
    conn_cfg = RTCConnConfig(
        client_role_type=ClientRoleType.CLIENT_ROLE_BROADCASTER,
        channel_profile=ChannelProfileType.CHANNEL_PROFILE_LIVE_BROADCASTING,
    )
    pub_cfg = RtcConnectionPublishConfig(
        audio_profile=AudioProfileType.AUDIO_PROFILE_DEFAULT,
        audio_scenario=AudioScenarioType.AUDIO_SCENARIO_AI_SERVER,
        is_publish_audio=True,   # Enable audio publishing for spoken summaries
        is_publish_video=False,
    )
    conn = agora_service.create_rtc_connection(conn_cfg, pub_cfg)

    # ── Step 8: Register audio frame observer ─────────────────────────────────
    observer = FrameCounterObserver(dg_manager)
    conn.register_audio_frame_observer(observer)
    logger.info("Audio frame observer registered with STT & spoken audio playback")

    # ── Step 9: Connect ────────────────────────────────────────────────────────
    conn.connect(token, channel_name, str(agent_uid))
    logger.info(
        f"Connected to Agora channel '{channel_name}' "
        f"(uid={agent_uid}, meeting={meeting_id[:8]}...)"
    )

    # Initialize mode and mute flags
    current_mode: str = "frequent"
    muted: bool = False
    _last_flag_check: float = time.monotonic()

    async def _load_meeting_flags(meeting_id: str) -> tuple[str, bool]:
        """Fetch default_mode and is_muted from the DB for the given meeting."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Meeting).where(Meeting.id == meeting_id))
            meeting = result.scalar_one_or_none()
            if meeting is None:
                logger.warning(f"Meeting {meeting_id} not found when loading flags")
                return "frequent", False
            mode = meeting.default_mode or "frequent"
            muted_flag = bool(meeting.is_muted)
            return mode, muted_flag

    # ── Step 10: Event loop with Cadence & Speech Guard ───────────────────────
    stop_event = asyncio.Event()

    def _on_signal(*_):
        logger.info("Shutdown signal received")
        loop.call_soon_threadsafe(stop_event.set)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, _on_signal)

    last_stats = time.monotonic()
    last_spoken_summary = time.monotonic()
    is_agent_speaking = False

    spoken_interval = int(os.environ.get("SPOKEN_SUMMARY_INTERVAL", DEFAULT_SPOKEN_INTERVAL))
    enable_spoken = os.environ.get("ENABLE_SPOKEN_SUMMARIES", "true").lower() == "true"

    try:
        while not stop_event.is_set():
            await asyncio.sleep(1)
            now = time.monotonic()

            # ── Periodic frame-count log ───────────────────────────────────────
            if now - last_stats >= STATS_INTERVAL:
                snap = observer.snapshot()
                if snap:
                    parts = " | ".join(
                        f"uid={uid}: {count} frames"
                        for uid, count in sorted(snap.items())
                    )
                    logger.info(f"[FRAMES meeting={meeting_id[:8]}] {parts}")
                last_stats = now

            # ── Periodic Spoken AI Voice Summary ──────────────────────────────
            if enable_spoken and not is_agent_speaking and (now - last_spoken_summary >= spoken_interval):
                # Refresh mode/mute flags every 30 seconds
                if now - _last_flag_check >= 30:
                    current_mode, muted = await _load_meeting_flags(meeting_id)
                    _last_flag_check = now
                # Skip summary if muted
                if muted:
                    logger.info("[SPOKEN SUMMARY] Skipped because Sentinel is muted")
                else:
                    # Speech Guard: Ensure human participants have not spoken in the last 2 seconds
                    human_silence_duration = now - observer.last_human_audio_ts
                    if human_silence_duration >= 2.0:
                        # Mode handling: frequent always, occasional only on high‑priority events (simplified to always for now)
                        if current_mode == "frequent" or (current_mode == "occasional" and False):
                            logger.info("[SPOKEN SUMMARY] Cadence reached & room quiet for %.1fs — generating spoken summary", human_silence_duration)
                            is_agent_speaking = True
                            try:
                                summary_text = generate_spoken_summary(meeting_id)
                                if summary_text:
                                    pcm_bytes = synthesize_speech(summary_text)
                                    if pcm_bytes:
                                        await play_spoken_summary_into_room(conn, pcm_bytes)
                                        last_spoken_summary = now
                            except Exception as exc:
                                logger.warning("Failed to play spoken summary: %s", exc)
                            finally:
                                is_agent_speaking = False
                        else:
                            logger.info("[SPOKEN SUMMARY] Ocassional mode – skipping periodic summary")
                    else:
                        logger.info("[SPOKEN SUMMARY] Deferring spoken summary — human active within last %.1fs", human_silence_duration)

            # ── Token renewal before expiry (55-minute mark) ──────────────────
            if app_cert and (now - token_ts) >= TOKEN_RENEWAL_AT:
                logger.info("Renewing Agora token...")
                new_token = _make_token(app_id, app_cert, channel_name, agent_uid)
                if new_token:
                    for method_name in ("renew_agora_token", "renew_token"):
                        fn = getattr(conn, method_name, None)
                        if fn is not None:
                            try:
                                fn(new_token)
                                token = new_token
                                token_ts = time.monotonic()
                                logger.info(f"Token renewed via conn.{method_name}()")
                                break
                            except Exception as exc:
                                logger.warning(f"conn.{method_name}() failed: {exc}")
    finally:
        logger.info("Disconnecting and cleaning up...")
        await dg_manager.stop()
        try:
            conn.disconnect()
            conn.release()
            agora_service.release()
            logger.info("Agent shut down cleanly")
        except Exception as exc:
            logger.warning("Shutdown error (non-fatal): %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agora AI Agent — headless channel participant with Deepgram STT & Spoken AI Summaries"
    )
    parser.add_argument("--meeting-id", required=True, help="Meeting UUID")
    args = parser.parse_args()

    try:
        meeting_id = str(uuid.UUID(args.meeting_id))
    except ValueError:
        logger.error(f"Invalid meeting-id '{args.meeting_id}' — must be a valid UUID")
        sys.exit(1)

    try:
        asyncio.run(run_agent(meeting_id))
    except Exception as exc:
        logger.exception(f"Agent crashed: {exc}")
        sys.exit(3)


if __name__ == "__main__":
    main()
