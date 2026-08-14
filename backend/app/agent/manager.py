"""
Agent process manager — spawns and monitors one subprocess per meeting.

Why a subprocess?
- agora_python_server_sdk enforces "one AgoraService instance per process".
- Crashes in the agent are fully isolated from the FastAPI process.
- Platform constraint: agora_python_server_sdk is Linux/macOS only.
  Launching it as a subprocess from FastAPI means the FastAPI process itself
  doesn't need to import the SDK; the subprocess will exit with code 2 on Windows.

Architecture:
    FastAPI process
    └── AgentManager.start_agent(meeting_id)
          └── asyncio.create_subprocess_exec(python runner.py --meeting-id ...)
                └── stdout piped → logged via [AGENT:<id>] prefix
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from app.config.settings import settings

logger = logging.getLogger(__name__)

# Absolute path to runner.py — resolved at import time
_RUNNER = Path(__file__).with_name("runner.py")


class AgentManager:
    """
    Singleton that tracks one agent subprocess per meeting.

    Thread-safety note: all public methods are called from an asyncio event loop;
    dict access is protected by the GIL so no additional locking is needed.
    """

    _instance: "AgentManager | None" = None

    def __new__(cls) -> "AgentManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # meeting_id (str) → asyncio.subprocess.Process
            cls._instance._procs: dict[str, asyncio.subprocess.Process] = {}
        return cls._instance

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_running(self, meeting_id: str) -> bool:
        proc = self._procs.get(meeting_id)
        return proc is not None and proc.returncode is None

    async def start_agent(self, meeting_id: str) -> dict:
        """
        Spawn the agent subprocess.

        Returns:
            {"started": True,  "pid": int}     — freshly started
            {"started": False, "reason": str}   — already running or launch error
        """
        if self.is_running(meeting_id):
            logger.info("Agent already running for meeting %s", meeting_id[:8])
            return {"started": False, "reason": "already_running"}

        # Evict any stale finished-process entry
        self._procs.pop(meeting_id, None)

        logger.info("Launching agent subprocess for meeting %s...", meeting_id[:8])
        env = os.environ.copy()
        env.update(
            {
                "AGORA_APP_ID": settings.agora_app_id,
                "AGORA_APP_CERTIFICATE": settings.agora_app_certificate,
                "DEEPGRAM_API_KEY": settings.deepgram_api_key,
                "ELEVENLABS_API_KEY": settings.elevenlabs_api_key,
                "GEMINI_API_KEY": settings.gemini_api_key,
                "GROQ_API_KEY": settings.groq_api_key,
                "DATABASE_URL": settings.database_url,
                "REDIS_URL": settings.redis_url,
                "ENABLE_SPOKEN_SUMMARIES": str(settings.enable_spoken_summaries).lower(),
                "SPOKEN_SUMMARY_INTERVAL": str(settings.spoken_summary_interval),
            }
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(_RUNNER),
                "--meeting-id", meeting_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except Exception as exc:
            logger.error("Failed to spawn agent for %s: %s", meeting_id[:8], exc)
            return {"started": False, "reason": str(exc)}

        self._procs[meeting_id] = proc
        logger.info("Agent subprocess started pid=%d for meeting %s", proc.pid, meeting_id[:8])

        # Background task: forward subprocess stdout to our structured logger
        asyncio.create_task(
            self._forward_logs(proc, meeting_id),
            name=f"agent-logs-{meeting_id[:8]}",
        )

        return {"started": True, "pid": proc.pid}

    async def stop_agent(self, meeting_id: str) -> bool:
        """Send SIGTERM to the running agent (graceful shutdown)."""
        proc = self._procs.get(meeting_id)
        if proc is None or proc.returncode is not None:
            return False
        try:
            proc.terminate()
            logger.info("Sent SIGTERM to agent pid=%d for meeting %s", proc.pid, meeting_id[:8])
            return True
        except Exception as exc:
            logger.warning("Could not terminate agent for %s: %s", meeting_id[:8], exc)
            return False

    def status(self, meeting_id: str) -> dict:
        proc = self._procs.get(meeting_id)
        if proc is None:
            return {"running": False, "pid": None, "returncode": None}
        return {
            "running": proc.returncode is None,
            "pid": proc.pid,
            "returncode": proc.returncode,
        }

    # ── Private ────────────────────────────────────────────────────────────────

    async def _forward_logs(self, proc: asyncio.subprocess.Process, meeting_id: str) -> None:
        """Pipe subprocess stdout lines into our logger with [AGENT:...] prefix."""
        short = meeting_id[:8]
        try:
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    logger.info("[AGENT:%s] %s", short, line)
        except Exception as exc:
            logger.warning("Log pipe error for meeting %s: %s", short, exc)
        finally:
            await proc.wait()
            code = proc.returncode
            level = logging.INFO if code in (0, -15) else logging.ERROR
            logger.log(level, "Agent for meeting %s exited (returncode=%d)", short, code)


# Module-level singleton — import and use directly
agent_manager = AgentManager()
