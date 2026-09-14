"""Round-3 audit verification: exercise each fix from the code/security audit.

  A. Zone-id IPv6 group / source rejected with a clear ValueError.
  B. Sender TTL validated (0..255) in core.
  C. stop() during a tight burst send loop produces NO spurious ERROR
     (the stop-race fix).
  D. Sender socket is closed after natural completion (fd == -1).
  E. Poller survives a JSON non-object response and reports an error
     snapshot; thread stays alive.
  F. Two simultaneous exporters each serve their own provider (no bleed).
  G. Receiver _make_socket failure does not leak the socket (best-effort:
     just verify the error propagates as before).
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from multicast_tool.core import (
    AddressFamily, MulticastReceiver, MulticastSender, ReceiverConfig,
    SenderConfig, SenderMode, parse_source_list,
)
from multicast_tool.remote import RemoteSenderPoller, StatsExporter
from multicast_tool.stats import StatsTracker


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def expect_value_error(fn, label) -> None:
    try:
        fn()
    except ValueError as e:
        assert "%" in str(e) or "range" in str(e) or "zone" in str(e), (label, str(e))
        print(f"OK: {label} -> ValueError: {str(e)[:80]}")
    else:
        raise AssertionError(f"{label}: expected ValueError")


def test_a_zone_id() -> None:
    cfg = ReceiverConfig(family=AddressFamily.IPV6, group="ff02::1%5")
    expect_value_error(
        lambda: MulticastReceiver(cfg, StatsTracker()).start(), "A1 zone-id group (receiver)")
    scfg = SenderConfig(family=AddressFamily.IPV6, group="ff3e::1%eth0")
    expect_value_error(
        lambda: MulticastSender(scfg).start(), "A2 zone-id group (sender)")
    expect_value_error(
        lambda: parse_source_list("fe80::1%5", AddressFamily.IPV6),
        "A3 zone-id source")
    # sanity: unscoped still fine
    assert parse_source_list("fe80::1", AddressFamily.IPV6) == ["fe80::1"]


def test_b_ttl() -> None:
    for bad in (-1, 256, 99999):
        scfg = SenderConfig(group="239.1.1.1", ttl=bad)
        expect_value_error(
            lambda s=scfg: MulticastSender(s).start(), f"B ttl={bad}")
    # valid TTL constructs fine
    s = MulticastSender(SenderConfig(group="239.1.1.1", ttl=255))
    assert s.config.ttl == 255
    del s


def test_c_stop_race() -> None:
    """Tight-loop burst; stop() from this thread mid-flight.

    Before the fix the worker could observe OSError from the closed
    socket and emit a send error; last_error must stay None now.
    """
    errors: list[str] = []
    sender = MulticastSender(SenderConfig(
        family=AddressFamily.IPV4, group="239.255.0.99", port=37801,
        ttl=0, payload_size=64, mode=SenderMode.CONTINUOUS, count=0,
        rate_pps=200000,  # as fast as possible
    ))
    sender.on_event = lambda ev: (
        errors.append(ev.message) if ev.type.value == "error" else None)
    sender.start()
    time.sleep(0.3)
    sender.stop()
    assert sender.last_error is None, f"spurious send error: {sender.last_error}"
    assert not errors, f"error events emitted on clean stop: {errors}"
    print("OK: C stop() during tight send -> no spurious ERROR "
          f"(sent={sender.sent_count()})")


def test_d_socket_closed_after_completion() -> None:
    sender = MulticastSender(SenderConfig(
        family=AddressFamily.IPV4, group="239.255.0.99", port=37802,
        ttl=0, payload_size=32, mode=SenderMode.BURST, count=5,
    ))
    sender.start()
    deadline = time.monotonic() + 5.0
    while sender.is_running() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not sender.is_running(), "burst did not finish"
    sock = sender._sock
    assert sock is not None
    assert sock.fileno() == -1, f"socket not closed after completion (fd={sock.fileno()})"
    print("OK: D sender socket closed after natural completion (fd=-1)")


def test_e_poller_non_dict_json() -> None:
    class _ArrHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps([1, 2, 3]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            return

    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _ArrHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        poller = RemoteSenderPoller(f"127.0.0.1:{port}", interval_sec=0.2)
        poller.start()
        deadline = time.monotonic() + 5.0
        snap = None
        while time.monotonic() < deadline:
            s = poller.snapshot()
            if s.status == "error":
                snap = s
                break
            time.sleep(0.1)
        assert snap is not None, f"no error snapshot; last={poller.snapshot()}"
        assert "unexpected response" in snap.error, snap.error
        assert poller.is_running, "poller thread died on hostile payload"
        poller.stop()
        print(f"OK: E poller survives JSON array response ({snap.error!r}), thread alive")
    finally:
        srv.shutdown(); srv.server_close()


def test_f_two_exporters() -> None:
    p1, p2 = _free_port(), _free_port()
    while p2 == p1:
        p2 = _free_port()
    ex1 = StatsExporter(p1, stats_provider=lambda: {"status": "one"})
    ex2 = StatsExporter(p2, stats_provider=lambda: {"status": "two"})
    ex1.start(); ex2.start()
    try:
        d1 = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{p1}/stats", timeout=2).read())
        d2 = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{p2}/stats", timeout=2).read())
        assert d1 == {"status": "one"}, d1
        assert d2 == {"status": "two"}, d2
        print("OK: F two simultaneous exporters serve their own providers")
    finally:
        ex1.stop(); ex2.stop()


def test_g_make_socket_error_propagates() -> None:
    cfg = ReceiverConfig(family=AddressFamily.IPV4, group="224.0.0.1",
                         port=1, interface="not-an-iface-xyz")
    rcv = MulticastReceiver(cfg, StatsTracker())
    try:
        rcv.start()
    except ValueError as e:
        print(f"OK: G unresolvable interface raises clean ValueError: {str(e)[:70]}")
    else:
        raise AssertionError("start() should fail on unresolvable interface")
    finally:
        rcv.stop()


def test_h_server_header() -> None:
    port = _free_port()
    ex = StatsExporter(port, stats_provider=lambda: {"status": "ok"})
    ex.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=2) as r:
            server_hdr = r.headers.get("Server", "")
        assert "Python" not in server_hdr and "BaseHTTP" not in server_hdr, server_hdr
        print(f"OK: H Server header does not disclose Python version ({server_hdr!r})")
    finally:
        ex.stop()


def main() -> int:
    tests = [test_a_zone_id, test_b_ttl, test_c_stop_race,
             test_d_socket_closed_after_completion, test_e_poller_non_dict_json,
             test_f_two_exporters, test_g_make_socket_error_propagates,
             test_h_server_header]
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
    print("\nAll round-3 audit verifications passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
