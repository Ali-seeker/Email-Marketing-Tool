"""Clock abstraction for deterministic time handling across the application.

All time-dependent logic must inject or call an instance of Clock instead
of calling datetime.now() directly, allowing tests to fake and step through time.
"""

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta


class Clock(ABC):
    """Abstract base clock interface."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current datetime (always UTC and timezone-aware)."""
        pass


class SystemClock(Clock):
    """Production clock returning system wall-clock UTC time."""

    def now(self) -> datetime:
        """Return real current UTC datetime."""
        return datetime.now(UTC)


class FakeClock(Clock):
    """Deterministic in-memory clock for testing time-dependent workflows."""

    def __init__(self, initial_time: datetime | None = None) -> None:
        """Initialize FakeClock with a fixed or provided UTC datetime.

        Args:
            initial_time: The starting datetime. If None, defaults to 2026-01-01T00:00:00Z.
        """
        if initial_time is None:
            self._current_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        elif initial_time.tzinfo is None:
            self._current_time = initial_time.replace(tzinfo=UTC)
        else:
            self._current_time = initial_time

    def now(self) -> datetime:
        """Return the simulated current UTC datetime."""
        return self._current_time

    def advance(self, duration: timedelta) -> None:
        """Advance simulated clock by a specific timedelta.

        Args:
            duration: The timedelta to add to current simulated time.
        """
        self._current_time += duration

    def set_time(self, new_time: datetime) -> None:
        """Set simulated clock to a specific datetime.

        Args:
            new_time: The new datetime to set.
        """
        if new_time.tzinfo is None:
            self._current_time = new_time.replace(tzinfo=UTC)
        else:
            self._current_time = new_time


# Default system clock singleton for dependency injection
system_clock = SystemClock()
