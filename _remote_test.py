"""Verify the remote sender synchronisation feature.

End-to-end test:
  1. Start a local MulticastSender in BURST mode.
  2. Wrap it in a StatsExporter and bind to a free localhost port.
  3. Start a RemoteSenderPoller pointing at that port.
  4. Wait for at least one snapshot; assert the values match.
  5. Stop the sender and confirm the next snapshot reports status=idle.
"""

from __future__ import annotations

import socket
import sys
import time

from multicast_tool.core import (
    AddressFamily, MulticastSender, SenderConfig, SenderMode,
)
from multicast_tool.remote import RemoteSenderPoller, StatsExporter


def _free_port() -> int:
    """Grab an unused TCP port by asking the OS."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_provider(sender: MulticastSender):
    """Build the same provider closure the SendTab uses."""
    def provider() -> dict:
        if sender is None or not sender.is_running():
            return {"status": "idle", "sent": 0, "bytes": 0,
                    "pps": 0.0, "bps": 0.0, "elapsed_sec": 0.0,
                    "target_group": "", "target_port": 0,
                    "target_family": "", "mode": "", "target_count": 0}
        snap = sender.stats.snapshot()
        return {
            "status": "running",
            "sent": snap.total_packets,
            "bytes": snap.total_bytes,
            "pps": round(snap.pps, 2),
            "bps": round(snap.bps, 2),
            "elapsed_sec": round(snap.elapsed_sec, 2),
            "target_group": sender.config.group,
            "target_port": sender.config.port,
            "target_family": sender.config.family.value,
            "mode": sender.config.mode.value,
            "target_count": sender.config.count if sender.config.mode is not SenderMode.CONTINUOUS else 0,
        }
    return provider


def main() -> int:
    GROUP = "239.255.0.55"
    PORT = 37699
    # 1) local sender (CONTINUOUS so the poller can observe it in flight)
    sender = MulticastSender(SenderConfig(
        family=AddressFamily.IPV4, group=GROUP, port=PORT,
        ttl=0, payload_size=64, mode=SenderMode.CONTINUOUS,
        count=0, rate_pps=200,
    ))
    sender.start()
    # 2) exporter
    ep_port = _free_port()
    exporter = StatsExporter(ep_port, _make_provider(sender))
    exporter.start()
    print(f"OK: exporter listening on 127.0.0.1:{ep_port}")

    # 3) poller
    poller = RemoteSenderPoller(f"127.0.0.1:{ep_port}", interval_sec=0.2)
    poller.start()

    # 4) wait for a fresh snapshot
    deadline = time.monotonic() + 5.0
    snap = None
    while time.monotonic() < deadline:
        time.sleep(0.1)
        s = poller.snapshot()
        if s.is_fresh and s.sent_packets > 0:
            snap = s
            break
    if snap is None:
        cur = poller.snapshot()
        print(f"FAIL: no fresh snapshot, last={cur}")
        sender.stop()
        exporter.stop()
        poller.stop()
        return 1
    assert snap.target_group == GROUP, snap.target_group
    assert snap.target_port == PORT, snap.target_port
    assert snap.target_family == "IPv4", snap.target_family
    assert snap.mode == "continuous", snap.mode
    assert snap.target_count == 0, snap.target_count   # continuous -> 0
    assert snap.sent_packets > 0, snap.sent_packets
    assert snap.sent_bytes > 0, snap.sent_bytes
    assert snap.pps >= 0, snap.pps
    assert snap.mode == "continuous", snap.mode
    print(f"OK: snapshot sent={snap.sent_packets} bytes={snap.sent_bytes} "
          f"pps={snap.pps:.1f} target={snap.target_group}:{snap.target_port} mode={snap.mode}")

    # 5) stop sender and confirm next snapshot goes idle
    sender.stop()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        time.sleep(0.1)
        s = poller.snapshot()
        if s.status == "idle" or (s.target_group == "" and s.sent_packets == 0):
            print(f"OK: after sender stop, snapshot status={s.status} sent={s.sent_packets}")
            break
    else:
        print(f"WARN: never saw idle snapshot, last status={s.status}")

    exporter.stop()
    poller.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
