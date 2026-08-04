"""Remote-stats synchronisation.

The sender side runs a tiny HTTP server (:class:`StatsExporter`) that
responds to ``GET /stats`` with a JSON snapshot of the active
:class:`~multicast_tool.core.MulticastSender`.

The receiver side runs a polling thread (:class:`RemoteSenderPoller`)
that periodically fetches that URL and caches the latest result.
The UI reads the cached value via :meth:`RemoteSenderPoller.snapshot`.

The protocol is intentionally trivial: a single HTTP GET, a single JSON
response. The whole feature works across machines, not just on the
loopback.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Sender side                                                                 #
# --------------------------------------------------------------------------- #


StatsProvider = Callable[[], dict]


# Strict host:port pattern. Rejects schemes, paths, IPv6 brackets, and any
# characters that could let an attacker trick the receiver into probing
# internal services (SSRF).
_HOST_PORT_RE = re.compile(r"^(?P<host>[A-Za-z0-9._-]+):(?P<port>\d{1,5})$")


def validate_host_port(host_port: str) -> tuple[str, int]:
    """Validate a ``host:port`` string. Return ``(host, port)`` or raise.

    Only the most common forms are accepted: an alphanumeric / dotted /
    dash hostname or IPv4 literal, followed by ``:`` and a 1-5 digit port.
    IPv6 literals must be supplied without brackets (we do not currently
    support bracket notation in the UI). Reject anything that looks like
    a URL, a file path, or an IPv6 address with a zone id.

    Loopback (127.0.0.0/8) and RFC1918 private addresses ARE allowed
    because the common use case is polling a sender on the same host
    or on the LAN; this is a tool the user runs themselves, not a
    public service, so SSRF via the address bar is not a meaningful
    threat. We still refuse multicast / link-local / reserved / 0.0.0.0
    because polling those addresses is always a mistake.

    Raises ``ValueError`` on bad input.
    """
    m = _HOST_PORT_RE.match((host_port or "").strip())
    if not m:
        raise ValueError(
            f"Invalid address {host_port!r}: expected 'host:port' "
            f"(letters, digits, dots, dashes; port 1-65535)"
        )
    host = m.group("host")
    port = int(m.group("port"))
    if not (1 <= port <= 65535):
        raise ValueError(f"Port out of range: {port}")
    try:
        addr = ipaddress.ip_address(host)
        # Reject IP families that would never be a legitimate /stats target
        if (addr.is_multicast or addr.is_link_local or addr.is_reserved
                or addr.is_unspecified):
            raise ValueError(
                f"Refusing to poll {host}: address is multicast / link-local "
                f"/ reserved / unspecified. Multicast senders should be on a "
                f"unicast IP."
            )
    except ValueError as e:
        if "Refusing to poll" in str(e):
            raise
        # Not an IP literal; let the network stack resolve it as a hostname
        pass
    return host, port


class _StatsHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler bound to a :class:`StatsExporter` at startup."""

    # Class-level binding: BaseHTTPRequestHandler is instantiated by
    # HTTPServer for every request, so we set the provider on the class
    # rather than on each instance.
    provider: Optional[StatsProvider] = None

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/stats":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"not found")
            return
        provider = _StatsHTTPHandler.provider
        if provider is None:
            payload = {"status": "no-provider"}
        else:
            try:
                payload = provider()
            except Exception:  # noqa: BLE001
                # Log full traceback locally but don't leak it over the wire
                logger.exception("stats provider failed")
                payload = {"status": "error", "error": "internal"}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        # Intentionally NOT sending Access-Control-Allow-Origin: this is a
        # JSON stats endpoint, not a public API. Browser-based attacks are
        # blocked by the same-origin policy.
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # Client gave up before we finished writing
            pass

    def log_message(self, format, *args) -> None:  # noqa: A002
        # Silence the default stderr access log
        return


class StatsExporter:
    """Run a small HTTP server that serves the sender's stats as JSON.

    By default binds to ``127.0.0.1`` so the server is reachable only
    from the local machine. Pass ``bind="0.0.0.0"`` to listen on all
    interfaces (LAN / internet).
    """

    DEFAULT_BIND = "127.0.0.1"

    def __init__(self, port: int, stats_provider: StatsProvider,
                 bind: str = DEFAULT_BIND) -> None:
        self.port = int(port)
        self.bind = bind
        self._stats_provider = stats_provider
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._bound_port: Optional[int] = None
        self._error: Optional[str] = None

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def bound_port(self) -> Optional[int]:
        return self._bound_port

    def start(self) -> None:
        if self._server is not None:
            return
        if not (1 <= self.port <= 65535):
            raise ValueError(f"Port out of range: {self.port}")
        _StatsHTTPHandler.provider = self._stats_provider
        try:
            server = ThreadingHTTPServer((self.bind, self.port), _StatsHTTPHandler)
        except OSError as e:
            self._error = f"Cannot bind {self.bind}:{self.port}: {e}"
            raise
        self._server = server
        self._bound_port = server.server_address[1]
        self._thread = threading.Thread(
            target=server.serve_forever,
            name=f"stats-exporter-{self.port}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # noqa: BLE001
                logger.exception("StatsExporter.stop")
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        self._bound_port = None
        if _StatsHTTPHandler.provider is self._stats_provider:
            _StatsHTTPHandler.provider = None


# --------------------------------------------------------------------------- #
# Receiver side                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class RemoteSenderSnapshot:
    """Latest snapshot received from a remote sender."""

    received_at: float = 0.0
    status: str = "idle"            # idle | running | error
    sent_packets: int = 0
    sent_bytes: int = 0
    pps: float = 0.0
    bps: float = 0.0
    elapsed_sec: float = 0.0
    target_group: str = ""
    target_port: int = 0
    target_family: str = ""          # "IPv4" | "IPv6"
    mode: str = ""                   # burst | rate | continuous
    target_count: int = 0
    error: str = ""

    @property
    def is_fresh(self) -> bool:
        """Return True if the snapshot was received within the last 5 seconds."""
        return self.received_at > 0 and (time.monotonic() - self.received_at) < 5.0

    @property
    def age_sec(self) -> float:
        if self.received_at == 0:
            return 0.0
        return time.monotonic() - self.received_at


class RemoteSenderPoller:
    """Periodically fetch stats from a remote :class:`StatsExporter`.

    The constructor validates ``host_port`` via
    :func:`validate_host_port`; bad input raises ``ValueError`` before
    any network I/O.
    """

    def __init__(self, host_port: str, interval_sec: float = 1.0) -> None:
        # Validate the address up front so a typo is reported before any
        # background thread is started.
        host, port = validate_host_port(host_port)
        self.host = host
        self.port = port
        self.host_port = f"{host}:{port}"  # canonicalised
        self.interval_sec = max(0.2, float(interval_sec))
        self._snapshot = RemoteSenderSnapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._connecting = False

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/stats"

    def snapshot(self) -> RemoteSenderSnapshot:
        with self._lock:
            # Return a shallow copy so the caller doesn't race with the poller
            return RemoteSenderSnapshot(**vars(self._snapshot))

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        with self._lock:
            self._snapshot = RemoteSenderSnapshot(status="connecting")
        self._thread = threading.Thread(
            target=self._poll_loop, name="remote-sender-poller", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        with self._lock:
            self._snapshot = RemoteSenderSnapshot(status="idle")

    def _poll_loop(self) -> None:
        url = self.url
        while not self._stop.is_set():
            try:
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw)
                with self._lock:
                    self._snapshot = _to_snapshot(data)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
                with self._lock:
                    self._snapshot = RemoteSenderSnapshot(
                        status="error", error=str(e)
                    )
            self._stop.wait(self.interval_sec)


def _to_snapshot(d: dict) -> RemoteSenderSnapshot:
    """Coerce a JSON-decoded dict into a :class:`RemoteSenderSnapshot`."""
    try:
        sent_packets = int(d.get("sent", d.get("sent_packets", 0)) or 0)
    except (TypeError, ValueError):
        sent_packets = 0
    try:
        sent_bytes = int(d.get("bytes", d.get("sent_bytes", 0)) or 0)
    except (TypeError, ValueError):
        sent_bytes = 0
    try:
        pps = float(d.get("pps", 0.0) or 0.0)
    except (TypeError, ValueError):
        pps = 0.0
    try:
        bps = float(d.get("bps", 0.0) or 0.0)
    except (TypeError, ValueError):
        bps = 0.0
    try:
        elapsed_sec = float(d.get("elapsed_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        elapsed_sec = 0.0
    try:
        target_port = int(d.get("target_port", 0) or 0)
    except (TypeError, ValueError):
        target_port = 0
    try:
        target_count = int(d.get("target_count", 0) or 0)
    except (TypeError, ValueError):
        target_count = 0
    return RemoteSenderSnapshot(
        received_at=time.monotonic(),
        status=str(d.get("status", "running") or "running"),
        sent_packets=sent_packets,
        sent_bytes=sent_bytes,
        pps=pps,
        bps=bps,
        elapsed_sec=elapsed_sec,
        target_group=str(d.get("target_group", "") or ""),
        target_port=target_port,
        target_family=str(d.get("target_family", "") or ""),
        mode=str(d.get("mode", "") or ""),
        target_count=target_count,
        error=str(d.get("error", "") or ""),
    )
