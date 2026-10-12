"""Background scheduler worker loop for ColdReach.

Runs outbound email dispatch, reply monitoring, and follow-up checks.
Can be invoked directly via: python -m app.worker
"""

import signal
import sys
import time

from app.core.clock import system_clock
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


class Worker:
    """Outreach worker managing scheduled tasks and dispatches."""

    def __init__(self) -> None:
        """Initialize worker process configuration."""
        self.settings = get_settings()
        self.clock = system_clock
        self._running = True

    def stop(self, *_) -> None:
        """Signal handler to terminate worker loop gracefully."""
        logger.info("Termination signal received. Stopping worker loop...")
        self._running = False

    def run_once(self) -> None:
        """Execute a single polling iteration for pending outreach tasks."""
        now = self.clock.now()
        logger.debug(
            "Worker tick at %s. LIVE_MODE=%s",
            now.isoformat(),
            self.settings.LIVE_MODE,
        )
        # Detailed task handlers (lead validation, dispatch, reply checks) will plug in here.

    def start(self) -> None:
        """Start the worker execution loop."""
        setup_logging()
        logger.info(
            "Worker started. SAFE_BY_DEFAULT active: LIVE_MODE=%s (dry-run=%s)",
            self.settings.LIVE_MODE,
            not self.settings.LIVE_MODE,
        )

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        try:
            while self._running:
                self.run_once()
                # Sleep briefly between ticks in live execution
                time.sleep(2)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received.")
        finally:
            logger.info("Worker process exited cleanly.")


if __name__ == "__main__":
    worker = Worker()
    worker.start()
    sys.exit(0)
