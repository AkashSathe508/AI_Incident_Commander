"""
Agora AI Agent Runner — standalone process.

Joins an Agora channel as a headless participant (UID in reserved range
900_000_001–999_999_999), subscribes to all remote audio streams, and
logs per-speaker frame counts every 5 seconds.

Usage (inside Docker container):
    python runner.py --meeting-id <uuid>

Platform: Linux / macOS (agora_python_server_sdk wraps a native .so).
          This script will exit with code 2 on Windows.

Important SDK constraint (from official README):
    "A process can only have one instance" — the AgoraService singleton
    lives for the entire lifetime of this process.
    "In all observers/callbacks, do NOT call SDK APIs." — we only
    increment counters inside on_playback_audio_frame_before_mixing.
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
    Uses psycopg2-binary (already in requirements) via SQLAlchemy sync engine.
    The DATABASE_URL may have '+asyncpg' driver suffix; strip it for sync use.
    """
    from sqlalchemy import create_engine, text  # type: ignore

    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    # Convert async driver URL → sync psycopg2 URL
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


# ── Audio frame observer ───────────────────────────────────────────────────────

class FrameCounterObserver:
    """
    Implements the Agora audio frame observer interface.

    on_playback_audio_frame_before_mixing is called for each decoded audio frame
    from a remote speaker BEFORE it is mixed with other streams — this gives us
    per-speaker attribution.

    ⚠️  Per Agora SDK docs: "Do NOT call SDK APIs inside callbacks."
        We only increment a counter (O(1), no allocation).
    """

    def __init__(self):
        self._counts: dict[int, int] = defaultdict(int)

    def snapshot(self) -> dict[int, int]:
        """Thread-safe-enough snapshot — CPython GIL protects dict reads."""
        return dict(self._counts)

    # ── Required observer interface methods ────────────────────────────────────
    # Return True (allow frame to pass through) for all callbacks we don't need.

    def on_record_audio_frame(self, audio_frame) -> bool:
        return True

    def on_playback_audio_frame(self, audio_frame) -> bool:
        return True

    def on_mixed_audio_frame(self, audio_frame) -> bool:
        return True

    def on_ear_monitoring_audio_frame(self, audio_frame) -> bool:
        return True

    def on_playback_audio_frame_before_mixing(self, uid, audio_frame) -> bool:
        """
        Called per remote speaker per audio frame (~every 10–20 ms).
        uid  — remote user's numeric UID (int or str depending on SDK version)
        audio_frame — PCM buffer object (not inspected here)
        """
        try:
            self._counts[int(uid)] += 1
        except Exception:
            pass  # silently ignore any type conversion failures
        return True  # allow frame to continue through the pipeline


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
    if token:
        logger.info("RTC token generated (AccessToken2, expires in 1 h)")
    else:
        logger.warning("No AGORA_APP_CERTIFICATE set — using null token (test mode only)")
    token_ts = time.monotonic()

    # ── Step 4: Import agora_python_server_sdk (Linux / macOS only) ───────────
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
        sys.exit(2)

    # ── Step 5: Initialize the Agora service (one per process) ────────────────
    logger.info("Initializing AgoraService...")
    agora_service = AgoraService()
    svc_cfg = AgoraServiceConfig()
    svc_cfg.appid = app_id
    svc_cfg.enable_audio_processor = True
    svc_cfg.enable_audio_device = False   # headless — no sound card
    svc_cfg.enable_video = False
    agora_service.initialize(svc_cfg)
    logger.info("AgoraService initialized")

    # ── Step 6: Configure and create RTC connection ────────────────────────────
    conn_cfg = RTCConnConfig(
        client_role_type=ClientRoleType.CLIENT_ROLE_BROADCASTER,
        channel_profile=ChannelProfileType.CHANNEL_PROFILE_LIVE_BROADCASTING,
    )
    pub_cfg = RtcConnectionPublishConfig(
        audio_profile=AudioProfileType.AUDIO_PROFILE_DEFAULT,
        audio_scenario=AudioScenarioType.AUDIO_SCENARIO_AI_SERVER,
        is_publish_audio=False,   # subscribe-only for now; set True to inject audio
        is_publish_video=False,
    )
    conn = agora_service.create_rtc_connection(conn_cfg, pub_cfg)

    # ── Step 7: Register audio frame observer ─────────────────────────────────
    observer = FrameCounterObserver()
    conn.register_audio_frame_observer(observer)
    logger.info("Audio frame observer registered")

    # ── Step 8: Connect ────────────────────────────────────────────────────────
    conn.connect(token, channel_name, str(agent_uid))
    logger.info(
        f"Connected to Agora channel '{channel_name}' "
        f"(uid={agent_uid}, meeting={meeting_id[:8]}...)"
    )

    # ── Step 9: Run event loop — stats every 5 s + token renewal at 55 min ────
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal(*_):
        logger.info("Shutdown signal received")
        loop.call_soon_threadsafe(stop_event.set)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, RuntimeError):
            # Windows or non-main-thread fallback
            signal.signal(sig, _on_signal)

    last_stats = time.monotonic()

    while not stop_event.is_set():
        await asyncio.sleep(1)
        now = time.monotonic()

        # ── Periodic frame-count log ───────────────────────────────────────────
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

        # ── Token renewal before expiry (55-minute mark) ──────────────────────
        if app_cert and (now - token_ts) >= TOKEN_RENEWAL_AT:
            logger.info("Renewing Agora token (55-minute mark reached)...")
            new_token = _make_token(app_id, app_cert, channel_name, agent_uid)
            if new_token:
                # Try both known method names; SDK source not fully accessible
                renewed = False
                for method_name in ("renew_agora_token", "renew_token"):
                    fn = getattr(conn, method_name, None)
                    if fn is not None:
                        try:
                            fn(new_token)
                            renewed = True
                            token = new_token
                            token_ts = time.monotonic()
                            logger.info(f"Token renewed via conn.{method_name}()")
                            break
                        except Exception as exc:
                            logger.warning(f"conn.{method_name}() failed: {exc}")
                if not renewed:
                    logger.warning(
                        "Token renewal failed — neither renew_agora_token() nor "
                        "renew_token() succeeded. Session may expire in ~5 minutes."
                    )

    # ── Step 10: Graceful shutdown ─────────────────────────────────────────────
    logger.info("Disconnecting...")
    try:
        conn.disconnect()
        conn.release()
        agora_service.release()
        logger.info("Agent shut down cleanly")
    except Exception as exc:
        logger.warning(f"Shutdown error (non-fatal): {exc}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agora AI Agent — headless channel participant"
    )
    parser.add_argument("--meeting-id", required=True, help="Meeting UUID")
    args = parser.parse_args()

    # Validate UUID format early
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
