"""Verify the security improvements that stay in multicast_tool.remote.

Kept (per current threat model):
  1. validate_host_port rejects malformed addresses (no scheme, no path,
     IPv6 brackets, etc.) before any network I/O.
  2. validate_host_port refuses multicast / link-local / reserved /
     0.0.0.0 IP literals to block obvious mistakes.
  3. StatsExporter defaults to binding 127.0.0.1, not 0.0.0.0, so
     a single-machine test run is not exposed to the LAN.
  4. do_GET does not return the provider's raw exception text -- the
     internal traceback is logged but the wire response only says
     "internal".

Reverted (over-engineered for a local test tool):
  - Bearer-token authentication (H2)
  - 64 KiB response size cap (M2)
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import urllib.request

from multicast_tool.remote import (
    StatsExporter,
    validate_host_port,
)


# --------------------------------------------------------------------------- #
# 1 & 2: validate_host_port                                                    #
# --------------------------------------------------------------------------- #


def test_validate_host_port() -> int:
    h, p = validate_host_port("example.com:8765")
    assert (h, p) == ("example.com", 8765), (h, p)
    h, p = validate_host_port("8.8.8.8:80")
    assert (h, p) == ("8.8.8.8", 80), (h, p)
    print("OK: validate_host_port accepts normal host:port")

    for bad in ["http://example.com:80", "https://example.com/", "file:///etc/passwd",
                "ftp://1.2.3.4", "//example.com", "example.com:80/extra"]:
        try:
            validate_host_port(bad)
        except ValueError:
            continue
        raise AssertionError(f"should have rejected {bad!r}")
    print("OK: validate_host_port rejects schemes / paths / weird inputs")

    for bad in ["[::1]:80", "fe80::1%eth0:80", ":80", "host:", "host:abc", "host:99999"]:
        try:
            validate_host_port(bad)
        except ValueError:
            continue
        raise AssertionError(f"should have rejected {bad!r}")
    print("OK: validate_host_port rejects IPv6 / port-range / empty input")

    for bad in ["169.254.0.1:80", "224.0.0.1:80", "240.0.0.1:80", "0.0.0.0:80"]:
        try:
            validate_host_port(bad)
        except ValueError as e:
            assert "Refusing" in str(e), str(e)
            continue
        raise AssertionError(f"should have refused bad target {bad!r}")
    print("OK: validate_host_port refuses link-local / multicast / reserved / 0.0.0.0")

    for good in ["127.0.0.1:80", "10.0.0.1:80", "192.168.1.1:80", "8.8.8.8:80"]:
        h, p = validate_host_port(good)
        assert p == 80, p
    print("OK: validate_host_port allows loopback / private / public unicast")
    return 0


# --------------------------------------------------------------------------- #
# 3: StatsExporter default bind                                                #
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_exporter_default_bind() -> int:
    """Default exporter binds to 127.0.0.1, not 0.0.0.0."""
    port = _free_port()
    ex = StatsExporter(port, stats_provider=lambda: {"status": "ok"})
    assert ex.bind == "127.0.0.1", ex.bind
    ex.start()
    try:
        assert ex.bound_port == port, (ex.bound_port, port)
        # And we can still GET /stats -- the endpoint is just loopback
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=2) as r:
            assert r.status == 200, r.status
            data = json.loads(r.read().decode("utf-8"))
            assert data == {"status": "ok"}, data
    finally:
        ex.stop()
    print("OK: default StatsExporter binds 127.0.0.1 and serves /stats")
    return 0


def test_exporter_network_bind() -> int:
    """When bound to 0.0.0.0 explicitly, the endpoint is reachable (opt-in)."""
    port = _free_port()
    ex = StatsExporter(port, stats_provider=lambda: {"status": "ok"}, bind="0.0.0.0")
    assert ex.bind == "0.0.0.0", ex.bind
    ex.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=2) as r:
            assert r.status == 200, r.status
    finally:
        ex.stop()
    print("OK: network-bound exporter is reachable (opt-in via bind parameter)")
    return 0


# --------------------------------------------------------------------------- #
# 4: do_GET does not leak provider exceptions                                   #
# --------------------------------------------------------------------------- #


def test_provider_error_sanitised() -> int:
    """When the provider raises, the wire response says 'internal' only.

    A misbehaving or malicious provider might raise an exception with
    a sensitive message (file paths, secrets, ...). The exception is
    logged locally with full traceback but the HTTP response only
    contains a fixed string.
    """
    def bad_provider():
        raise RuntimeError("/etc/shadow: contains super-secret")
    port = _free_port()
    ex = StatsExporter(port, stats_provider=bad_provider)
    ex.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=2) as r:
            assert r.status == 200, r.status
            data = json.loads(r.read().decode("utf-8"))
        assert data == {"status": "error", "error": "internal"}, data
        # And the sensitive string must NOT appear in the response
        assert "/etc/shadow" not in json.dumps(data)
    finally:
        ex.stop()
    print("OK: provider exceptions are sanitised; traceback stays local")
    return 0


# --------------------------------------------------------------------------- #
# entry                                                                        #
# --------------------------------------------------------------------------- #


def main() -> int:
    rc = 0
    for fn in [
        test_validate_host_port,
        test_exporter_default_bind,
        test_exporter_network_bind,
        test_provider_error_sanitised,
    ]:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL [{fn.__name__}]: {e}")
            rc = 1
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            print(f"ERR [{fn.__name__}]: {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
