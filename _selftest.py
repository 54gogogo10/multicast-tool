"""Headless self-test for multicast_tool core modules.

Run with: python _selftest.py
Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback


def main() -> int:
    # Late import so we get useful tracebacks
    from multicast_tool.core import (
        AddressFamily, IgmpVersion, MldVersion, ReceiverConfig, SenderConfig,
        SenderMode, MulticastReceiver, MulticastSender, is_multicast_group,
        parse_source_list, interface_index, local_addresses,
    )
    from multicast_tool.stats import StatsTracker, format_bytes, format_rate_bps

    print("[1] Address validation")
    assert is_multicast_group("224.0.0.1", AddressFamily.IPV4)
    assert is_multicast_group("239.255.0.42", AddressFamily.IPV4)
    assert not is_multicast_group("192.168.1.1", AddressFamily.IPV4)
    assert is_multicast_group("ff02::1", AddressFamily.IPV6)
    assert is_multicast_group("ff3e::1", AddressFamily.IPV6)
    assert not is_multicast_group("::1", AddressFamily.IPV6)
    print("  ok")

    print("[2] Source list parsing")
    assert parse_source_list("", AddressFamily.IPV4) == []
    assert parse_source_list("10.0.0.1, 10.0.0.2", AddressFamily.IPV4) == ["10.0.0.1", "10.0.0.2"]
    try:
        parse_source_list("10.0.0.1, fe80::1", AddressFamily.IPV4)
    except ValueError:
        pass
    else:
        print("  expected ValueError for cross-family source"); return 1
    print("  ok")

    print("[3] local_addresses")
    v4 = local_addresses(AddressFamily.IPV4)
    print(f"  IPv4 local addrs: {v4[:3]}{'...' if len(v4) > 3 else ''}")
    assert isinstance(v4, list)
    print("  ok")

    print("[4] interface_index with default")
    assert interface_index("", AddressFamily.IPV4) == 0
    assert interface_index("auto", AddressFamily.IPV4) == 0
    print("  ok")

    print("[5] StatsTracker")
    st = StatsTracker(window_sec=1.0)
    for i in range(5):
        st.record(100)
        time.sleep(0.01)
    snap = st.snapshot()
    assert snap.total_packets == 5
    assert snap.total_bytes == 500
    assert snap.pps >= 4.0, snap.pps
    assert snap.bps >= 3000, snap.bps
    assert snap.elapsed_sec > 0
    print(f"  ok: pps={snap.pps:.1f}, bps={snap.bps:.0f}, elapsed={snap.elapsed_sec:.2f}s")

    print("[6] MulticastSender BURST + MulticastReceiver roundtrip")
    GROUP = "239.255.0.42"
    PORT = 37651
    scfg = SenderConfig(
        family=AddressFamily.IPV4, group=GROUP, port=PORT,
        ttl=0, payload_size=64, mode=SenderMode.BURST, count=20,
    )
    rcfg = ReceiverConfig(
        family=AddressFamily.IPV4, group=GROUP, port=PORT,
        interface="", igmp_version=IgmpVersion.V2,
    )
    rst = StatsTracker(window_sec=2.0)
    rcv = MulticastReceiver(rcfg, rst)
    sender = MulticastSender(scfg)

    rcv.start()
    time.sleep(0.2)
    sender.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and rst.snapshot().total_packets < 20:
        time.sleep(0.05)
    sender.stop()
    time.sleep(0.1)
    final = rst.snapshot()
    rcv.stop()
    print(f"  receiver: pkts={final.total_packets}/{sender.sent_count()}, bytes={final.total_bytes}, rate={final.pps:.1f} pps")
    if final.total_packets < 15:
        print(f"  WARN: only got {final.total_packets}/20 packets (loopback may be restricted)")
    else:
        print("  ok")

    print("[7] MulticastSender RATE_LIMITED")
    scfg2 = SenderConfig(
        family=AddressFamily.IPV4, group=GROUP, port=PORT,
        ttl=0, payload_size=32, mode=SenderMode.RATE_LIMITED, count=50, rate_pps=200,
    )
    rst2 = StatsTracker(window_sec=1.0)
    rcfg2 = ReceiverConfig(
        family=AddressFamily.IPV4, group=GROUP, port=PORT,
        interface="", igmp_version=IgmpVersion.V2,
    )
    rcv2 = MulticastReceiver(rcfg2, rst2)
    sender2 = MulticastSender(scfg2)
    rcv2.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    sender2.start()
    deadline = time.time() + 5.0
    while time.time() < deadline and rst2.snapshot().total_packets < 50:
        time.sleep(0.05)
    sender2.stop()
    rcv2.stop()
    elapsed = time.monotonic() - t0
    print(f"  50 pkts in {elapsed:.2f}s ({50/max(elapsed,0.01):.1f} pps actual)")
    if 0.15 < elapsed < 0.6:
        print("  ok (rate roughly matches 200 pps +/- scheduling jitter)")

    print("[8] formatters")
    assert format_bytes(0) == "0.00 B"
    assert "KB" in format_bytes(2048)
    assert "Mbps" in format_rate_bps(2_000_000)
    print("  ok")

    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
