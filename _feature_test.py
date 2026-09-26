"""Feature verification for the loss-detection / DSCP / CSV-export trio.

  1. Payload header layout (magic + session + seq + ts, 20 bytes).
  2. SeqTracker semantics: in-order, gap, duplicate, reorder, 32-bit wrap,
     session split, stream cap (spoof flood), reset, aggregate snapshot.
  3. Sender-receiver UDP roundtrip: loss snapshot reports zero loss and
     tracks every packet.
  4. Template mode: fixed payloads are NOT sequence-tracked.
  5. DSCP validation (0..63) and IP_TOS / IPV6_TCLASS option packing.
  6. CSV export: content roundtrip, formula-injection sanitising, BOM.
  7. UI wiring: table has one column per header key / default width.
"""
from __future__ import annotations

import os
import socket
import struct
import sys
import tempfile
import time
import traceback

from multicast_tool.core import (
    PAYLOAD_HEADER_SIZE, PAYLOAD_MAGIC, AddressFamily, IgmpVersion, LossSnapshot,
    MulticastReceiver, MulticastSender, ReceiverConfig, SeqTracker, SenderConfig,
    SenderMode, _PAYLOAD_HEADER,
)
from multicast_tool.stats import StatsTracker, csv_safe_cell, write_csv


def test_1_header_layout() -> None:
    assert PAYLOAD_MAGIC == b"MCT1"
    assert PAYLOAD_HEADER_SIZE == 20
    blob = _PAYLOAD_HEADER.pack(PAYLOAD_MAGIC, 0x11223344, 0xDEADBEEF & 0xFFFFFFFF, 1234.5)
    assert len(blob) == 20
    magic, session, seq, ts = _PAYLOAD_HEADER.unpack(blob)
    assert (magic, session, seq, ts) == (PAYLOAD_MAGIC, 0x11223344, 0xDEADBEEF, 1234.5)
    print("OK: 1 payload header layout (!4sIId, 20 bytes)")


def test_2_seq_tracker() -> None:
    t = SeqTracker()
    # in-order
    for i in range(10):
        t.feed("10.0.0.1", 7, i)
    s = t.snapshot()
    assert (s.streams, s.received, s.lost, s.out_of_order, s.duplicates) == (1, 10, 0, 0, 0), s
    # gap: 10,11,12,13 never arrived (4 packets)
    t.feed("10.0.0.1", 7, 14)
    s = t.snapshot()
    assert s.lost == 4 and s.received == 11, s
    assert abs(s.loss_pct - 4.0 / 15.0 * 100.0) < 1e-9
    # late packet 12 fills a hole that was already counted as lost:
    # out_of_order +1, loss compensated to 3 (11,13 still missing... plus 10)
    t.feed("10.0.0.1", 7, 12)
    s = t.snapshot()
    assert s.out_of_order == 1 and s.lost == 3 and s.received == 12, s
    # in-order 15 must NOT be miscounted as loss after the reorder
    t.feed("10.0.0.1", 7, 15)
    s = t.snapshot()
    assert s.lost == 3 and s.received == 13, s
    # duplicate of an already-received old packet (bit is set now)
    t.feed("10.0.0.1", 7, 12)
    assert t.snapshot().duplicates == 1
    # a packet from further back than the bitmap window is a stale
    # duplicate and must not change the loss estimate
    t.feed("10.0.0.1", 7, 15 - 64)
    s = t.snapshot()
    assert s.lost == 3 and s.duplicates == 2, s
    # new session on the same source -> a fresh stream, no phantom loss
    t.feed("10.0.0.1", 8, 0)
    s = t.snapshot()
    assert s.streams == 2, s
    # second source tracked independently
    t.feed("10.0.0.2", 7, 100)
    assert t.snapshot().streams == 3

    # 32-bit wrap: stream runs through the top of the range into 0
    w = SeqTracker()
    w.feed("h", 1, 0xFFFFFFFD)
    w.feed("h", 1, 0xFFFFFFFE)
    w.feed("h", 1, 0xFFFFFFFF)
    w.feed("h", 1, 0)          # wrap continuation: in order
    w.feed("h", 1, 1)
    s = w.snapshot()
    assert (s.received, s.lost, s.out_of_order) == (5, 0, 0), s
    # wrap WITH a gap: 0xFFFFFFFD then 1 (missing FFFFFFFE/F, 0) -> 3 lost
    w2 = SeqTracker()
    w2.feed("h", 1, 0xFFFFFFFD)
    w2.feed("h", 1, 1)
    assert w2.snapshot().lost == 3, w2.snapshot()

    # stream cap: spoofing fresh sessions must not grow the table forever
    capped = SeqTracker(max_streams=8)
    for sess in range(64):
        capped.feed("attacker", sess, 1)
    assert capped.snapshot().streams == 8

    # reset clears everything
    t.reset()
    assert t.snapshot() == LossSnapshot(), t.snapshot()
    print("OK: 2 SeqTracker in-order/gap/dup/reorder/wrap/session/cap/reset")


def test_3_roundtrip_no_loss() -> None:
    GROUP, PORT = "239.255.0.77", 37901
    scfg = SenderConfig(
        family=AddressFamily.IPV4, group=GROUP, port=PORT,
        ttl=0, payload_size=128, mode=SenderMode.BURST, count=25,
    )
    rcfg = ReceiverConfig(
        family=AddressFamily.IPV4, group=GROUP, port=PORT,
        interface="", igmp_version=IgmpVersion.V2,
    )
    rcv = MulticastReceiver(rcfg, StatsTracker())
    sender = MulticastSender(scfg)
    rcv.start()
    time.sleep(0.2)
    sender.start()
    deadline = time.time() + 4.0
    while time.time() < deadline and sender.is_running():
        time.sleep(0.05)
    time.sleep(0.2)
    loss = rcv.loss_snapshot()
    rcv.stop()
    assert loss.received >= 15, f"roundtrip received too few tracked packets: {loss}"
    assert loss.lost == 0, f"unexpected loss on loopback: {loss}"
    assert loss.duplicates == 0 and loss.out_of_order == 0, loss
    print(f"OK: 3 roundtrip tracks {loss.received} packets, 0 lost, "
          f"{loss.streams} stream(s)")


def test_4_template_untracked() -> None:
    scfg = SenderConfig(
        family=AddressFamily.IPV4, group="239.255.0.78", port=37902,
        ttl=0, payload_size=64, mode=SenderMode.BURST, count=5,
        payload_template=b"fixed-text",
    )
    s = MulticastSender(scfg)
    payload = s._build_payload()
    assert isinstance(payload, bytes), "template payload must stay immutable bytes"
    assert payload.startswith(b"fixed"), payload[:16]
    # small default payload: too short for a header, stays untracked
    s2 = MulticastSender(SenderConfig(payload_size=8))
    small = s2._build_payload()
    assert isinstance(small, bytes) and len(small) == 8
    # default payload is a mutable bytearray with the header
    s3 = MulticastSender(SenderConfig(payload_size=64))
    buf = s3._build_payload()
    assert isinstance(buf, bytearray) and len(buf) == 64
    magic, _sess, _seq, _ts = _PAYLOAD_HEADER.unpack_from(bytes(buf), 0)
    assert magic == PAYLOAD_MAGIC
    print("OK: 4 template/small payloads untracked; default payload carries header")


def test_5_dscp() -> None:
    # validation
    for bad in (-1, 64, 999):
        try:
            MulticastSender(SenderConfig(group="239.1.1.1", dscp=bad)).start()
        except ValueError as e:
            assert "DSCP" in str(e), str(e)
        else:
            raise AssertionError(f"dscp={bad} should have raised")
    # option packing via a capture socket, per platform
    class _CaptureSock:
        def __init__(self):
            self.calls = []

        def setsockopt(self, level, opt, value):
            self.calls.append((level, opt, value))

    from multicast_tool import core as _core

    def _configure(cfg, platform):
        s = _CaptureSock()
        real_platform, sys.platform = sys.platform, platform
        try:
            MulticastSender(cfg)._configure_socket(s)
        finally:
            sys.platform = real_platform
        return s.calls

    tos = 46 << 2  # EF
    v4_calls = _configure(
        SenderConfig(family=AddressFamily.IPV4, group="239.1.1.1", dscp=46), "win32")
    assert (socket.IPPROTO_IP, socket.IP_TOS, tos) in v4_calls, v4_calls
    v6_calls = _configure(
        SenderConfig(family=AddressFamily.IPV6, group="ff3e::1", dscp=46), "linux")
    assert (socket.IPPROTO_IPV6, _core.IPV6_TCLASS, tos) in v6_calls, v6_calls
    # dscp=0 -> no marking options at all
    plain_calls = _configure(
        SenderConfig(family=AddressFamily.IPV4, group="239.1.1.1", dscp=0), "win32")
    assert all(opt != socket.IP_TOS for _lvl, opt, _v in plain_calls)
    print("OK: 5 DSCP validated (0-63) and packed into IP_TOS / IPV6_TCLASS")


def test_6_csv_export() -> None:
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        header = ["#", "Group", "Loss"]
        rows = [
            ["1", "239.1.1.1", "0 (0.00%)"],
            ["2", "=HYPERLINK(\"http://evil\")", "+SUM(A1:A9)"],
            ["3", "中文组播", "@cmd"],
        ]
        n = write_csv(path, header, rows)
        assert n == 3
        with open(path, "rb") as f:
            raw = f.read()
        assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM for Excel"
        text = raw.decode("utf-8-sig")
        assert "'=HYPERLINK" in text and "'+SUM" in text and "'@cmd" in text
        assert "中文组播" in text
        assert "239.1.1.1" in text and "'239" not in text
    finally:
        os.unlink(path)
    # sanitiser is value-preserving for normal data
    assert csv_safe_cell(12) == "12"
    assert csv_safe_cell("00:42") == "00:42"
    assert csv_safe_cell("-") == "'-"
    print("OK: 6 CSV export roundtrip, formula injection neutralised, BOM present")


def test_7_ui_wiring() -> None:
    from multicast_tool.ui import ReceiveTab, SendTab
    assert len(ReceiveTab.DEFAULT_WIDTHS) == len(ReceiveTab.HEADER_KEYS) == 12
    assert ReceiveTab.HEADER_KEYS.index("col.loss") == 10
    assert "send.dscp" in _i18n_keys() and "recv.btn_export_csv" in _i18n_keys()
    assert hasattr(SendTab, "retranslate_ui")
    print("OK: 7 UI table has 12 columns with Loss at index 10")


def _i18n_keys() -> set:
    from multicast_tool import i18n
    return set(i18n.STRINGS["zh_CN"]) | set(i18n.STRINGS["en"])


def main() -> int:
    tests = [test_1_header_layout, test_2_seq_tracker, test_3_roundtrip_no_loss,
             test_4_template_untracked, test_5_dscp, test_6_csv_export,
             test_7_ui_wiring]
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
    print("\nAll feature tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
