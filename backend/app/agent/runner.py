"""
Agora AI Agent Runner — standalone process with Deepgram Real-Time STT.

Joins an Agora channel as a headless participant (UID in reserved range
900_000_001–999_999_999), subscribes to all remote audio streams, streams
per-speaker PCM audio into Deepgram Live STT, persists normalized transcript
segments to PostgreSQL, and broadcasts live segments to WebSocket clients.

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
from collections import defaultdict
from typing import Any

import httpx

from app.ingestion.transcript_ingestor import transcript_ingestor

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


# ── Token helper ───────────────────────────────────────────────────────────────

def _make_token(app_id: str, app_cert: str, channel: str, uid: int) -> str | None:
    """Generate Agora AccessToken2. Returns None when cert is not configured."""
    if not app_cert or not app_id:
        return None
    try:
        from agora_token_builder import RtcTokenBuilder  # type: ignore
        return RtcTokenBuilder.build_token_with_uid(
            app_id, app_cert, channel, uid,
            role=1,                       # Role_Publisher
            token_expire=TOKEN_EXPIRY_SECONDS,
            privilege_expire=0,
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

    async def start() -> None:
        """Start the background audio processing worker."""
        self._httpx_client = httpx.AsyncClient(timeout=5.0)
        self._worker_task = asyncio.create_task(self._process_audio_queue())
        logger.info("Deepgram manager started for meeting %s", self.meeting_id[:8])

    async def stop() -> None:
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
                            # Broadcast payload to backend WebSocket clients
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


# ── Audio frame observer ───────────────────────────────────────────────────────

class FrameCounterObserver:
    """
    Implements the Agora audio frame observer interface.

    on_playback_audio_frame_before_mixing is called for each decoded audio frame
    from a remote speaker BEFORE it is mixed with other streams.
    """

    def __init__(self, dg_manager: DeepgramManager | None = None):
        self._counts: dict[int, int] = defaultdict(int)
        self.dg_manager = dg_manager

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

            if self.dg_manager and hasattr(audio_frame, "buffer"):
                buf = audio_frame.buffer
                if buf:
                    pcm_bytes = bytes(buf)
                    self.dg_manager.enqueue_audio(n_uid, pcm_bytes)
        except Exception:
            pass
        return True


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

    # ── Step 7: Configure and create RTC connection ────────────────────────────
    conn_cfg = RTCConnConfig(
        client_role_type=ClientRoleType.CLIENT_ROLE_BROADCASTER,
        channel_profile=ChannelProfileType.CHANNEL_PROFILE_LIVE_BROADCASTING,
    )
    pub_cfg = RtcConnectionPublishConfig(
        audio_profile=AudioProfileType.AUDIO_PROFILE_DEFAULT,
        audio_scenario=AudioScenarioType.AUDIO_SCENARIO_AI_SERVER,
        is_publish_audio=False,
        is_publish_video=False,
    )
    conn = agora_service.create_rtc_connection(conn_cfg, pub_cfg)

    # ── Step 8: Register audio frame observer ─────────────────────────────────
    observer = FrameCounterObserver(dg_manager)
    conn.register_audio_frame_observer(observer)
    logger.info("Audio frame observer registered with Deepgram STT stream integration")

    # ── Step 9: Connect ────────────────────────────────────────────────────────
    conn.connect(token, channel_name, str(agent_uid))
    logger.info(
        f"Connected to Agora channel '{channel_name}' "
        f"(uid={agent_uid}, meeting={meeting_id[:8]}...)"
    )

    # ── Step 10: Event loop ───────────────────────────────────────────────────
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

    try:
        while not stop_event.is_set():
            await asyncio.sleep(1)
            now = time.monotonic()

            if now - last_stats >= STATS_INTERVAL:
                snap = observer.snapshot()
                if snap:
                    parts = " | ".join(
                        f"uid={uid}: {count} frames"
                        for uid, count in sorted(snap.items())
                    )
                    logger.info(f"[FRAMES meeting={meeting_id[:8]}] {parts}")
                else:
                    logger.info(
                        f"[FRAMES meeting={meeting_id[:8]}] "
                        "No remote audio yet — waiting for speakers..."
                    )
                last_stats = now

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
            logger.warning(f"Shutdown error (non-fatal): {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agora AI Agent — headless channel participant with Deepgram STT"
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
