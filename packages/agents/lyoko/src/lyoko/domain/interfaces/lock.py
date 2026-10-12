"""Distributed lock port interface."""

from abc import ABC, abstractmethod


class DistributedLock(ABC):
    """Port for distributed lock and leader election coordinators."""

    @abstractmethod
    async def acquire(self, timeout: float | None = None) -> bool:
        """Attempt to acquire the lock, waiting up to timeout seconds (or forever if None).

        Returns True if acquired, False if timed out or failed.
        """

    @abstractmethod
    async def release(self) -> None:
        """Release the acquired lock and free underlying resources."""

    @property
    @abstractmethod
    def is_locked(self) -> bool:
        """Check whether the current instance holds the lock."""
