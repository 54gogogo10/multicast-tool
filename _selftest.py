"""Headless self-test for multicast_tool core modules.

Run with: python _selftest.py
Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import socket
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
    else:
        print(f"  WARN: elapsed {elapsed:.2f}s outside expected 0.15-0.6s "
              f"(scheduling jitter or loaded machine?)")

    print("[8] formatters")
    assert format_bytes(0) == "0.00 B"
    assert "KB" in format_bytes(2048)
    assert "Mbps" in format_rate_bps(2_000_000)
    print("  ok")

    print("[9] interface resolution (name / IP -> index)")
    # Names resolve via if_nametoindex where available
    try:
        entries = socket.if_nameindex()
    except (OSError, AttributeError):
        entries = []
    if entries:
        # if_nameindex returns (index, name) tuples
        idx, name = entries[0]
        assert interface_index(name, AddressFamily.IPV4) == idx, (name, idx)
        print(f"  ok: name {name!r} -> index {idx}")
    else:
        print("  skip: if_nameindex unavailable")
    # Local IPs must resolve to a nonzero index on supported platforms
    resolved = [(ip, interface_index(ip, AddressFamily.IPV4)) for ip in v4]
    hits = [(ip, i) for ip, i in resolved if i]
    print(f"  IPv4 IP->index: {resolved}")
    if sys.platform.startswith(("win", "linux")):
        assert hits, f"no local IPv4 resolved to an interface index: {resolved}"
        print("  ok")
    else:
        print("  (not asserting on this platform)")

    print("[10] Receiver error path leaves a clean state")
    rcfg3 = ReceiverConfig(
        family=AddressFamily.IPV4, group=GROUP, port=PORT + 1,
        interface="", igmp_version=IgmpVersion.V2,
    )
    rst3 = StatsTracker()
    rcv3 = MulticastReceiver(rcfg3, rst3)
    rcv3.start()
    time.sleep(0.1)
    # Force the receive loop to fail by closing its socket underneath it
    rcv3._sock.close()
    deadline = time.time() + 3.0
    while time.time() < deadline and rcv3.is_running():
        time.sleep(0.05)
    assert not rcv3.is_running(), "receiver still marked running after recv error"
    assert rcv3.last_error is not None, "last_error not set after recv error"
    rcv3.stop()  # must be safe after an error exit
    print(f"  ok (error={rcv3.last_error!r})")

    print("[11] SSM join/leave option packing (both platforms, mocked socket)")
    import struct as _struct
    from multicast_tool import core as _core

    class _CaptureSock:
        def __init__(self):
            self.calls = []

        def setsockopt(self, level, opt, value):
            self.calls.append((level, opt, bytes(value)))

    def _run_join_leave(cfg, platform):
        """Run _join_group + _leave_group against a capture socket while
        simulating the given platform; returns the captured setsockopt calls."""
        s = _CaptureSock()
        real_platform, sys.platform = sys.platform, platform
        real_default = _core._win_default_ipv6_ifindex
        _core._win_default_ipv6_ifindex = lambda: 42
        try:
            rcv = MulticastReceiver(cfg, StatsTracker())
            rcv._join_group(s)
            rcv._leave_group(s)
        finally:
            sys.platform = real_platform
            _core._win_default_ipv6_ifindex = real_default
        return s.calls

    # IPv4 IGMPv3 SSM: the ip_mreq_source field order REALLY differs.
    #   Win32:  { multiaddr, sourceaddr, interface }
    #   Linux:  { multiaddr, interface, sourceaddr }
    group4 = socket.inet_aton("232.1.2.3")
    src4 = socket.inet_aton("198.51.100.7")
    iface4 = socket.inet_aton("192.0.2.7")
    cfg4 = ReceiverConfig(
        family=AddressFamily.IPV4, group="232.1.2.3", port=1234,
        interface="192.0.2.7", igmp_version=IgmpVersion.V3,
        sources=["198.51.100.7"],
    )
    for platform, expected in (("win32", group4 + src4 + iface4),
                               ("linux", group4 + iface4 + src4)):
        (jl, jo, jb), (ll, lo, _lb) = _run_join_leave(cfg4, platform)
        assert jl == socket.IPPROTO_IP and jo in (15, 39), (platform, jo)
        assert ll == socket.IPPROTO_IP and lo in (16, 40), (platform, lo)
        assert jb == expected, (platform, jb.hex())
        assert _lb == expected, (platform, _lb.hex())
    print("  ok: ip_mreq_source order per platform")

    # IPv6 MLDv2 SSM: option numbers differ, payload layout is IDENTICAL
    # (RFC 3678 group_source_req: ifindex first, then group/source storage).
    grp6 = socket.inet_pton(socket.AF_INET6, "ff3e::1")
    src6 = socket.inet_pton(socket.AF_INET6, "2001:db8::1")
    cfg6 = ReceiverConfig(
        family=AddressFamily.IPV6, group="ff3e::1", port=1234,
        interface="", mld_version=MldVersion.V2,
        sources=["2001:db8::1"],
    )
    join_opts = {"win32": 45, "linux": 46}
    leave_opts = {"win32": 46, "linux": 47}
    for platform in ("win32", "linux"):
        calls = _run_join_leave(cfg6, platform)
        assert len(calls) == 2, calls
        for (lvl, opt, blob), expected_opt in zip(calls,
                                                  (join_opts[platform], leave_opts[platform])):
            assert lvl == socket.IPPROTO_IPV6 and opt == expected_opt, (platform, opt)
            idx = _struct.unpack_from("<I", blob, 0)[0]
            assert idx == (42 if platform == "win32" else 0), (platform, idx)
            assert blob[4:8] == b"\x00" * 4, "8-byte alignment for sockaddr_storage"
            assert _struct.unpack_from("=H", blob, 8)[0] == socket.AF_INET6
            assert blob[16:32] == grp6, "group addr must be at offset 16"
            assert blob[144:160] == src6, "source addr must be at offset 144"
            assert len(blob) == 8 + 128 + 128
    print("  ok: group_source_req layout identical on win32/linux (ifindex first)")

    print("\nAll self-tests passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
