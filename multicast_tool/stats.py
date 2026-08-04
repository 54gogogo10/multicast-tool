"""Thread-safe sliding-window statistics tracker for multicast receivers.

Tracks per-receiver packet/byte counters and computes real-time pps / bps
over a configurable sliding window (default 1 second).
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class StatsSnapshot:
    """Immutable snapshot of a stats tracker at a point in time."""

    total_packets: int
    total_bytes: int
    pps: float          # packets per second (1s window)
    bps: float          # bits per second (1s window)
    elapsed_sec: float  # seconds since start


class StatsTracker:
    """Thread-safe stats for a single multicast receiver.

    The receiver thread calls :meth:`record` for each packet; the GUI thread
    calls :meth:`snapshot` periodically to render the table.
    """

    __slots__ = (
        "_lock",
        "_start_time",
        "_total_packets",
        "_total_bytes",
        "_samples",
        "_window_sec",
    )

    def __init__(self, window_sec: float = 1.0) -> None:
        self._lock = threading.Lock()
        self._start_time = time.monotonic()
        self._total_packets = 0
        self._total_bytes = 0
        # deque of (mono_time, packet_delta, byte_delta)
        self._samples: collections.deque[tuple[float, int, int]] = collections.deque()
        self._window_sec = float(window_sec)

    def record(self, n_bytes: int) -> None:
        """Record one received packet of ``n_bytes`` bytes."""
        now = time.monotonic()
        with self._lock:
            self._total_packets += 1
            self._total_bytes += n_bytes
            self._samples.append((now, 1, n_bytes))
            self._trim_locked(now)

    def reset(self) -> None:
        """Reset all counters and the start time."""
        with self._lock:
            self._start_time = time.monotonic()
            self._total_packets = 0
            self._total_bytes = 0
            self._samples.clear()

    def snapshot(self) -> StatsSnapshot:
        """Return a consistent snapshot of the current stats."""
        now = time.monotonic()
        with self._lock:
            self._trim_locked(now)
            cutoff = now - self._window_sec
            pkts = 0
            bts = 0
            for t, dp, db in self._samples:
                if t >= cutoff:
                    pkts += dp
                    bts += db
            return StatsSnapshot(
                total_packets=self._total_packets,
                total_bytes=self._total_bytes,
                pps=float(pkts) / self._window_sec if self._window_sec > 0 else 0.0,
                bps=float(bts) * 8.0 / self._window_sec if self._window_sec > 0 else 0.0,
                elapsed_sec=now - self._start_time,
            )

    def _trim_locked(self, now: float) -> None:
        cutoff = now - self._window_sec * 2  # keep 2x window for headroom
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


def format_bytes(n: int) -> str:
    """Format a byte count as a human-readable string."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:,.2f} {unit}"
        n /= 1024.0
    return f"{n:,.2f} PB"


def format_rate_bps(bps: float) -> str:
    """Format a bits-per-second value as a human-readable string."""
    v = float(bps)
    for unit in ("bps", "Kbps", "Mbps", "Gbps", "Tbps"):
        if v < 1000.0:
            return f"{v:,.2f} {unit}"
        v /= 1000.0
    return f"{v:,.2f} Tbps"
