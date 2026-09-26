"""Round-4 audit verification (post-feature hardening series).

  A. Reorder-after-gap: a late packet that fills a hole compensates the
     loss estimate; in-order packets after it are NOT double-counted
     (the phantom-loss fix).
  B. Stale retransmissions from beyond the bitmap window never touch the
     loss estimate.
  C. write_csv failures raise (the UI handler catches broadly).
  D. Hostile payloads through _track_seq: truncated / wrong-magic /
     non-tuple addresses are ignored without killing the receive loop.
  E. Hostile session-id flood cannot grow the tracker past its cap.
  F. DSCP config applies on a real IPv4 socket; IPv6 degrade path never
     raises even when the platform rejects IPV6_TCLASS.
  G. Exported CSV keeps data rows intact after sanitising (roundtrip).
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import traceback

from multicast_tool.core import (
    MAX_SEQ_STREAMS, PAYLOAD_MAGIC, AddressFamily, MulticastReceiver,
    MulticastSender, ReceiverConfig, SeqTracker, SenderConfig,
)
from multicast_tool.stats import StatsTracker, write_csv


def test_a_phantom_loss() -> None:
    t = SeqTracker()
    for i in range(10):
        t.feed("src", 1, i)
    t.feed("src", 1, 14)      # gap: 10-13 lost
    t.feed("src", 1, 12)      # late, fills the hole
    t.feed("src", 1, 15)      # in-order successor
    s = t.snapshot()
    assert (s.received, s.lost, s.out_of_order, s.duplicates) == (13, 3, 1, 0), s
    t.feed("src", 1, 16)
    assert t.snapshot().lost == 3
    print("OK: A no phantom loss after reorder-hole-fill")


def test_b_stale_retransmission() -> None:
    t = SeqTracker()
    for i in range(100):
        t.feed("src", 1, i)
    before = t.snapshot()
    for old in (0, 5, 35):    # 100-35=65 >= window 64 for the last one
        t.feed("src", 1, old)
    s = t.snapshot()
    assert s.lost == before.lost and s.received == before.received, (before, s)
    assert s.duplicates == 3
    print("OK: B stale retransmissions counted as duplicates, loss untouched")


def test_c_write_csv_raises() -> None:
    # Writing to a *directory* must raise (IsADirectoryError /
    # PermissionError, both OSError subclasses); the UI handler catches
    # broadly either way.
    d = tempfile.mkdtemp(prefix="mc_csv_dir_")
    try:
        write_csv(d, ["x"], [["1"]])
    except OSError:
        print("OK: C write_csv raises OSError on a directory path")
    else:
        raise AssertionError("write_csv to a directory should raise")
    finally:
        os.rmdir(d)


def test_d_hostile_packets() -> None:
    rcfg = ReceiverConfig(family=AddressFamily.IPV4, group="224.0.0.99", port=1)
    rcv = MulticastReceiver(rcfg, StatsTracker())
    cases = [
        b"",                                    # empty
        b"\x00",                                # 1 byte
        b"MCT1",                                # magic only, truncated
        b"MCT1" + b"\x00" * 15,                 # 19 bytes (one short)
        b"XXXX" + b"\xff" * 16,                 # wrong magic, full length
        b"\x00" * 20,                           # all-zero (magic mismatch)
        b"MCT1" + b"\xff" * 40000,              # oversized, valid header
    ]
    for payload in cases:
        rcv._track_seq(payload, ("10.9.9.9", 1234))
    rcv._track_seq(b"MCT1" + b"\x00" * 16, None)   # hostile address shape
    s = rcv.loss_snapshot()
    # Exactly the two well-formed payloads got tracked (the oversized one
    # too -- length is not a validity criterion); nothing raised.
    assert s.streams == 2 and s.received == 2, s
    assert rcv.stats.snapshot().total_packets == 0
    print(f"OK: D hostile payloads handled without error (tracked={s.received})")


def test_e_session_flood_cap() -> None:
    t = SeqTracker()
    for sess in range(2000):
        t.feed("attacker", sess, 1)
    assert t.snapshot().streams == MAX_SEQ_STREAMS
    print(f"OK: E session flood capped at {MAX_SEQ_STREAMS} streams")


def test_f_dscp_real_sockets() -> None:
    sn = MulticastSender(SenderConfig(
        family=AddressFamily.IPV4, group="239.1.1.1", dscp=46))
    s4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sn._configure_socket(s4)
        tos = s4.getsockopt(socket.IPPROTO_IP, socket.IP_TOS)
        assert tos == 46 << 2, tos
    finally:
        s4.close()
    # IPv6: platform may reject IPV6_TCLASS (Winsock); configure must not raise
    sn6 = MulticastSender(SenderConfig(
        family=AddressFamily.IPV6, group="ff3e::1", dscp=46))
    s6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    try:
        sn6._configure_socket(s6)
    except OSError as e:
        raise AssertionError(f"IPv6 DSCP degrade path raised: {e}")
    finally:
        s6.close()
    print("OK: F DSCP applied on IPv4; IPv6 degrade path does not raise")


def test_g_csv_roundtrip_after_sanitise() -> None:
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        rows = [["1", "239.1.1.1", "0 (0.00%)"], ["2", "-0.00", "@x"]]
        write_csv(path, ["#", "g", "loss"], rows)
        import csv as _csv
        with open(path, encoding="utf-8-sig", newline="") as f:
            got = list(_csv.reader(f))
        assert got[0] == ["#", "g", "loss"]
        assert got[1] == ["1", "239.1.1.1", "0 (0.00%)"]
        assert got[2] == ["2", "'-0.00", "'@x"]
    finally:
        os.unlink(path)
    print("OK: G CSV roundtrip: headers/data intact, risky cells quoted")


def main() -> int:
    tests = [test_a_phantom_loss, test_b_stale_retransmission,
             test_c_write_csv_raises, test_d_hostile_packets,
             test_e_session_flood_cap, test_f_dscp_real_sockets,
             test_g_csv_roundtrip_after_sanitise]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception:
            traceback.print_exc()
            print(f"FAIL [{fn.__name__}]")
            failed += 1
    if failed:
        print(f"\n{failed} test(s) FAILED")
        return 1
    print("\nAll round-4 audit verifications passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
