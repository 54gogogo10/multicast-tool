"""Multicast send / receive core (IGMP for IPv4, MLD for IPv6).

This module is intentionally UI-agnostic so it can be tested or embedded
elsewhere. Both :class:`MulticastReceiver` and :class:`MulticastSender` use
non-blocking background threads and surface lifecycle events through a
simple :class:`ReceiverEvent` / :class:`SenderEvent` callback.
"""

from __future__ import annotations

import enum
import ipaddress
import logging
import random
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Union

from .stats import StatsTracker

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Enums and configuration                                                     #
# --------------------------------------------------------------------------- #


class AddressFamily(str, enum.Enum):
    IPV4 = "IPv4"
    IPV6 = "IPv6"


class IgmpVersion(str, enum.Enum):
    """IGMP version. v3 enables source-specific semantics when sources
    are provided (SSM). On v1/v2 sources are ignored (ASM only)."""

    V1 = "IGMPv1"
    V2 = "IGMPv2"
    V3 = "IGMPv3"


class MldVersion(str, enum.Enum):
    """MLD version. v2 enables source-specific semantics when sources
    are provided. On v1 sources are ignored."""

    V1 = "MLDv1"
    V2 = "MLDv2"


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def is_multicast_group(group: str, family: AddressFamily) -> bool:
    """Return True if ``group`` is a valid multicast address for the family."""
    try:
        addr = ipaddress.ip_address(group)
    except ValueError:
        return False
    if family is AddressFamily.IPV4:
        return isinstance(addr, ipaddress.IPv4Address) and addr.is_multicast
    if family is AddressFamily.IPV6:
        return isinstance(addr, ipaddress.IPv6Address) and addr.is_multicast
    return False


def parse_source_list(text: str, family: AddressFamily) -> list[str]:
    """Parse a comma- or whitespace-separated list of source IPs.

    Returns the list with empty entries stripped. Raises ValueError on
    malformed entries or mismatched address family.
    """
    if not text or not text.strip():
        return []
    tokens = [t.strip() for t in text.replace(",", " ").split() if t.strip()]
    out: list[str] = []
    for tok in tokens:
        try:
            addr = ipaddress.ip_address(tok)
        except ValueError as e:
            raise ValueError(f"Invalid source IP: {tok!r}") from e
        if family is AddressFamily.IPV4 and not isinstance(addr, ipaddress.IPv4Address):
            raise ValueError(f"Source {tok!r} is not IPv4")
        if family is AddressFamily.IPV6 and not isinstance(addr, ipaddress.IPv6Address):
            raise ValueError(f"Source {tok!r} is not IPv6")
        out.append(str(addr))
    return out


def interface_index(name_or_ip: str, family: AddressFamily) -> int:
    """Resolve a local interface name or IP to a numeric interface index.

    Empty string or 'auto' / 'default' returns 0 (kernel default).
    Accepts both interface names (e.g. 'eth0', 'Wi-Fi') and IPs.
    """
    s = (name_or_ip or "").strip()
    if not s or s.lower() in ("auto", "default", "*", "0"):
        return 0
    # 1) Try direct name lookup
    try:
        idx = socket.if_nametoindex(s)
        if idx:
            return idx
    except (OSError, AttributeError):
        pass
    # 2) Maybe the user passed an IP; find the interface that owns it and
    #    resolve that name back to an index.
    try:
        target = ipaddress.ip_address(s)
    except ValueError:
        return 0
    name = _iface_name_for_ip(str(target), family)
    if name:
        try:
            return socket.if_nametoindex(name)
        except OSError:
            return 0
    return 0


def _af(family: AddressFamily) -> int:
    return socket.AF_INET if family is AddressFamily.IPV4 else socket.AF_INET6


def _iface_name_for_ip(ip: str, family: AddressFamily) -> Optional[str]:
    """Best-effort lookup of an interface name that owns ``ip``.

    Uses the UDP-probe trick to learn the local interface for a given
    remote, then walks the interface list to find the matching name.
    Falls back to ``None`` on platforms where the data is unavailable.
    """
    af = _af(family)
    # 1) Open a UDP socket, "connect" to a public IP so the kernel picks
    #    a local source, then read getsockname() to find the local IP.
    try:
        with socket.socket(af, socket.SOCK_DGRAM) as s:
            probe_host = "8.8.8.8" if family is AddressFamily.IPV4 else "2001:4860:4860::8888"
            s.connect((probe_host, 80))
            local = s.getsockname()[0]
    except OSError:
        return None
    if family is AddressFamily.IPV6 and "%" in local:
        local = local.split("%", 1)[0]
    if local != ip:
        return None
    # 2) We know this IP is the system's primary outgoing IP, but the
    #    kernel does not expose a portable "ip -> ifname" mapping in
    #    stdlib. The caller can still use the IP as the interface.
    return None


def local_addresses(family: AddressFamily) -> list[str]:
    """Enumerate local interface IPs for a given family.

    Uses :func:`socket.gethostbyname_ex` for IPv4 (Windows-compatible)
    and :func:`socket.getaddrinfo` with the local hostname for IPv6.
    Falls back to a UDP-connect trick (``connect`` to a public IP) if
    neither yields results, so the caller always gets at least one
    candidate to bind / set as outgoing interface.
    """
    out: list[str] = []
    seen: set[str] = set()
    hostname = socket.gethostname()
    af = _af(family)

    if family is AddressFamily.IPV4:
        # gethostbyname_ex is the most reliable on Windows for IPv4
        try:
            _, aliases, ips = socket.gethostbyname_ex(hostname)
            for ip in ips:
                if ip not in seen:
                    seen.add(ip)
                    out.append(ip)
            for alias in aliases:
                try:
                    _, _, ips2 = socket.gethostbyname_ex(alias)
                except OSError:
                    continue
                for ip in ips2:
                    if ip not in seen:
                        seen.add(ip)
                        out.append(ip)
        except OSError:
            pass
    else:
        # IPv6: walk getaddrinfo results for the local hostname
        try:
            for info in socket.getaddrinfo(hostname, None, family=af):
                sockaddr = info[4]
                ip = sockaddr[0].split("%", 1)[0]
                if ip not in seen:
                    seen.add(ip)
                    out.append(ip)
        except OSError:
            pass

    if not out:
        # Fallback: open a UDP socket and "connect" to a public address so
        # the kernel picks a local interface for us. The actual connect
        # doesn't send any traffic.
        probe_host = "8.8.8.8" if family is AddressFamily.IPV4 else "2001:4860:4860::8888"
        try:
            with socket.socket(af, socket.SOCK_DGRAM) as s:
                s.connect((probe_host, 80))
                local = s.getsockname()[0]
                if family is AddressFamily.IPV6 and "%" in local:
                    local = local.split("%", 1)[0]
                if local and local not in seen:
                    out.append(local)
        except OSError:
            pass
    return out


# --------------------------------------------------------------------------- #
# Receiver                                                                    #
# --------------------------------------------------------------------------- #


@dataclass
class ReceiverConfig:
    """Parameters for a single multicast receive session."""

    family: AddressFamily = AddressFamily.IPV4
    group: str = "224.0.0.1"
    port: int = 5000
    interface: str = ""           # iface name, IP, or "auto"
    igmp_version: IgmpVersion = IgmpVersion.V2
    mld_version: MldVersion = MldVersion.V2
    sources: list[str] = field(default_factory=list)  # for v3/MLDv2 SSM
    recv_buffer_size: int = 1 << 20  # 1 MiB


class ReceiverEventType(str, enum.Enum):
    STARTED = "started"
    STOPPED = "stopped"
    PACKET = "packet"
    ERROR = "error"


@dataclass
class ReceiverEvent:
    type: ReceiverEventType
    message: str = ""
    source_addr: Optional[str] = None
    n_bytes: int = 0


EventCallback = Callable[[ReceiverEvent], None]


class MulticastReceiver:
    """Receive multicast packets for one group:port and feed :class:`StatsTracker`."""

    def __init__(
        self,
        config: ReceiverConfig,
        stats: StatsTracker,
        on_event: Optional[EventCallback] = None,
    ) -> None:
        self.config = config
        self.stats = stats
        self.on_event = on_event
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._joined = False
        self._error: Optional[str] = None

    # -- public ------------------------------------------------------------ #

    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    def start(self) -> None:
        if self._running.is_set():
            return
        if not is_multicast_group(self.config.group, self.config.family):
            raise ValueError(f"Not a valid {self.config.family.value} multicast address: {self.config.group!r}")
        if not (1 <= self.config.port <= 65535):
            raise ValueError(f"Port out of range: {self.config.port}")
        # Validate source list (will raise on bad input)
        for s in self.config.sources:
            ipaddress.ip_address(s)  # raises on bad

        sock = self._make_socket()
        try:
            self._join_group(sock)
            self._joined = True
        except Exception:
            sock.close()
            raise

        self._sock = sock
        self._running.set()
        self._thread = threading.Thread(
            target=self._recv_loop, name=f"mc-recv-{self.config.group}", daemon=True
        )
        self._thread.start()
        self._emit(ReceiverEventType.STARTED, f"Joined {self.config.group} on {self.config.interface or 'default'}")

    def stop(self) -> None:
        if not self._running.is_set():
            return
        self._running.clear()
        # Closing the socket unblocks recvfrom on all platforms
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                # Drop the group before close (best effort)
                if self._joined:
                    try:
                        self._leave_group(sock)
                    except Exception:  # noqa: BLE001
                        logger.debug("leave_group failed", exc_info=True)
                sock.close()
            except OSError:
                pass
        self._joined = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._emit(ReceiverEventType.STOPPED, "Receiver stopped")

    # -- internals --------------------------------------------------------- #

    def _emit(self, etype: ReceiverEventType, message: str = "", **kw) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(ReceiverEvent(type=etype, message=message, **kw))
        except Exception:  # noqa: BLE001
            logger.exception("Receiver event callback failed")

    def _make_socket(self) -> socket.socket:
        family = _af(self.config.family)
        sock = socket.socket(family, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            pass
        # On Linux, SO_REUSEPORT helps multiple receivers coexist
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.config.recv_buffer_size)
        # Bind to the multicast group on Linux/macOS for filtering; on Windows
        # binding to the group address can fail on some stacks, so we bind
        # to the wildcard address there. We try the group first, fall back.
        # The fallback is logged because it changes what the socket
        # receives (any traffic to the port, not just multicast), and the
        # user should know.
        bind_host = self.config.group if self.config.family is AddressFamily.IPV4 else ""
        try:
            sock.bind((bind_host, self.config.port))
        except OSError as e:
            logger.warning(
                "could not bind to %s:%d (%s); falling back to wildcard -- "
                "this socket will also receive unicast traffic on that port",
                bind_host or "<unspecified>", self.config.port, e,
            )
            if self.config.family is AddressFamily.IPV6:
                sock.bind(("::", self.config.port))
            else:
                sock.bind(("0.0.0.0", self.config.port))
        sock.settimeout(0.5)  # allow periodic running-flag check
        return sock

    def _join_group(self, sock: socket.socket) -> None:
        if self.config.family is AddressFamily.IPV4:
            self._join_ipv4(sock)
        else:
            self._join_ipv6(sock)

    def _leave_group(self, sock: socket.socket) -> None:
        if self.config.family is AddressFamily.IPV4:
            self._leave_ipv4(sock)
        else:
            self._leave_ipv6(sock)

    def _join_ipv4(self, sock: socket.socket) -> None:
        group_bin = socket.inet_aton(self.config.group)
        iface = self.config.interface.strip()
        if_index = interface_index(iface, self.config.family) if iface else 0
        # Resolve the local interface address used for membership reporting.
        if iface and if_index == 0:
            # Treat as an IP
            try:
                ipaddress.ip_address(iface)
                ifaddr = socket.inet_aton(iface)
            except ValueError:
                # Not a valid IP -- fallback to INADDR_ANY
                ifaddr = socket.inet_aton("0.0.0.0")
        elif if_index:
            ifaddr = socket.inet_aton("0.0.0.0")  # let kernel pick the right one
        else:
            ifaddr = socket.inet_aton("0.0.0.0")
        if self.config.igmp_version is IgmpVersion.V3 and self.config.sources:
            # IP_ADD_SOURCE_MEMBERSHIP (IGMPv3 SSM)
            for src in self.config.sources:
                src_bin = socket.inet_aton(src)
                mreq = struct.pack("!4s4s4s", group_bin, ifaddr, src_bin)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_SOURCE_MEMBERSHIP, mreq)
        else:
            mreq = struct.pack("!4s4s", group_bin, ifaddr)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    def _leave_ipv4(self, sock: socket.socket) -> None:
        group_bin = socket.inet_aton(self.config.group)
        iface = self.config.interface.strip()
        if iface:
            try:
                ifaddr = socket.inet_aton(iface)
            except OSError:
                ifaddr = socket.inet_aton("0.0.0.0")
        else:
            ifaddr = socket.inet_aton("0.0.0.0")
        if self.config.igmp_version is IgmpVersion.V3 and self.config.sources:
            for src in self.config.sources:
                src_bin = socket.inet_aton(src)
                mreq = struct.pack("!4s4s4s", group_bin, ifaddr, src_bin)
                try:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_SOURCE_MEMBERSHIP, mreq)
                except OSError:
                    pass
        else:
            mreq = struct.pack("!4s4s", group_bin, ifaddr)
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
            except OSError:
                pass

    def _join_ipv6(self, sock: socket.socket) -> None:
        # Resolve interface index (numeric)
        iface = self.config.interface.strip()
        idx = interface_index(iface, self.config.family) if iface else 0
        group_bin = socket.inet_pton(socket.AF_INET6, self.config.group)
        if self.config.mld_version is MldVersion.V2 and self.config.sources:
            # IPV6_JOIN_SOURCE_GROUP (MLDv2 SSM)
            for src in self.config.sources:
                src_bin = socket.inet_pton(socket.AF_INET6, src)
                # struct ipv6_mreq_source: 16s address, 4s index, 16s source_addr
                mreq = group_bin + struct.pack("I", idx) + src_bin
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_SOURCE_GROUP, mreq)
        else:
            mreq = group_bin + struct.pack("I", idx)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, mreq)

    def _leave_ipv6(self, sock: socket.socket) -> None:
        iface = self.config.interface.strip()
        idx = interface_index(iface, self.config.family) if iface else 0
        group_bin = socket.inet_pton(socket.AF_INET6, self.config.group)
        if self.config.mld_version is MldVersion.V2 and self.config.sources:
            for src in self.config.sources:
                src_bin = socket.inet_pton(socket.AF_INET6, src)
                mreq = group_bin + struct.pack("I", idx) + src_bin
                try:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_LEAVE_SOURCE_GROUP, mreq)
                except OSError:
                    pass
        else:
            mreq = group_bin + struct.pack("I", idx)
            try:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_LEAVE_GROUP, mreq)
            except OSError:
                pass

    def _recv_loop(self) -> None:
        assert self._sock is not None
        sock = self._sock
        while self._running.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as e:
                if not self._running.is_set():
                    break
                self._error = str(e)
                self._emit(ReceiverEventType.ERROR, f"recv error: {e}")
                break
            self.stats.record(len(data))
            self._emit(
                ReceiverEventType.PACKET,
                source_addr=addr[0] if addr else None,
                n_bytes=len(data),
            )


# --------------------------------------------------------------------------- #
# Sender                                                                      #
# --------------------------------------------------------------------------- #


class SenderMode(str, enum.Enum):
    BURST = "burst"           # send N packets as fast as possible
    RATE_LIMITED = "rate"     # send at fixed pps
    CONTINUOUS = "continuous" # send until stopped


@dataclass
class SenderConfig:
    family: AddressFamily = AddressFamily.IPV4
    group: str = "239.1.1.1"
    port: int = 5000
    interface: str = ""          # iface name/IP for outgoing interface
    source: str = ""            # optional source IP for IP_MULTICAST_IF / IPV6_MULTICAST_IF
    ttl: int = 1                # TTL for IPv4, Hop Limit for IPv6
    payload_size: int = 1024
    mode: SenderMode = SenderMode.BURST
    count: int = 1000           # for burst / target for rate-limited
    rate_pps: int = 1000        # for rate-limited
    payload_template: bytes = b""  # if non-empty, sent as-is; else random bytes


class SenderEventType(str, enum.Enum):
    STARTED = "started"
    STOPPED = "stopped"
    PROGRESS = "progress"
    ERROR = "error"


@dataclass
class SenderEvent:
    type: SenderEventType
    message: str = ""
    sent: int = 0
    target: int = 0


SenderCallback = Callable[[SenderEvent], None]


class MulticastSender:
    """Send multicast packets in burst, rate-limited, or continuous mode."""

    def __init__(self, config: SenderConfig, on_event: Optional[SenderCallback] = None) -> None:
        self.config = config
        self.on_event = on_event
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._stop_requested = threading.Event()
        self._sent = 0
        self._error: Optional[str] = None
        # Stats for the optional remote-monitor feature
        self.stats: StatsTracker = StatsTracker()

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    def is_running(self) -> bool:
        return self._running.is_set()

    def sent_count(self) -> int:
        return self._sent

    def start(self) -> None:
        if self._running.is_set():
            return
        if not is_multicast_group(self.config.group, self.config.family):
            raise ValueError(f"Not a valid {self.config.family.value} multicast address: {self.config.group!r}")
        if not (1 <= self.config.port <= 65535):
            raise ValueError(f"Port out of range: {self.config.port}")
        if self.config.payload_size < 0 or self.config.payload_size > 65507:
            raise ValueError(f"Payload size out of range: {self.config.payload_size}")

        sock = self._make_socket()
        self._sock = sock
        self._sent = 0
        self._stop_requested.clear()
        self._running.set()
        self._thread = threading.Thread(
            target=self._send_loop, name=f"mc-send-{self.config.group}", daemon=True
        )
        self._thread.start()
        self._emit(SenderEventType.STARTED, f"Sending to {self.config.group}:{self.config.port}")

    def stop(self) -> None:
        if not self._running.is_set():
            return
        self._stop_requested.set()
        self._running.clear()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._emit(SenderEventType.STOPPED, f"Sender stopped ({self._sent} sent)")

    def _emit(self, etype: SenderEventType, message: str = "", **kw) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(SenderEvent(type=etype, message=message, **kw))
        except Exception:  # noqa: BLE001
            logger.exception("Sender event callback failed")

    def _make_socket(self) -> socket.socket:
        family = _af(self.config.family)
        sock = socket.socket(family, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        if self.config.family is AddressFamily.IPV4:
            # Set outgoing interface
            if self.config.source:
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                    socket.inet_aton(self.config.source),
                )
            elif self.config.interface:
                # If interface is an IP, use it
                try:
                    ipaddress.ip_address(self.config.interface)
                    sock.setsockopt(
                        socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                        socket.inet_aton(self.config.interface),
                    )
                except ValueError:
                    pass
            # TTL
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.config.ttl)
            # Loopback enabled by default; leave as-is
        else:  # IPv6
            if self.config.source or self.config.interface:
                # Convert to interface index
                idx = interface_index(
                    self.config.source or self.config.interface, self.config.family
                )
                if idx:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, idx)
            # Hop Limit
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, self.config.ttl)
        # Use a non-zero send buffer for higher throughput
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
        return sock

    def _send_loop(self) -> None:
        assert self._sock is not None
        sock = self._sock
        target = (self.config.group, self.config.port)
        payload = self._build_payload()
        sent = 0
        target_count: Optional[int]
        if self.config.mode is SenderMode.BURST:
            target_count = max(0, int(self.config.count))
        elif self.config.mode is SenderMode.RATE_LIMITED:
            target_count = max(0, int(self.config.count))
        else:
            target_count = None  # continuous

        interval = 0.0
        # Both Rate-limited and Continuous honour the rate; Burst does not.
        if self.config.mode in (SenderMode.RATE_LIMITED, SenderMode.CONTINUOUS) \
                and self.config.rate_pps > 0:
            interval = 1.0 / float(self.config.rate_pps)

        next_send = time.monotonic()
        progress_step = max(1, (target_count or 1000) // 100) if target_count else 100
        last_progress_sent = 0
        payload_len = len(payload)

        try:
            while not self._stop_requested.is_set():
                if target_count is not None and sent >= target_count:
                    break
                try:
                    sock.sendto(payload, target)
                except OSError as e:
                    self._error = str(e)
                    self._emit(SenderEventType.ERROR, f"send error: {e}")
                    break
                sent += 1
                self._sent = sent
                self.stats.record(payload_len)
                if sent - last_progress_sent >= progress_step:
                    last_progress_sent = sent
                    self._emit(
                        SenderEventType.PROGRESS, sent=sent, target=target_count or 0
                    )
                if interval > 0:
                    next_send += interval
                    sleep_for = next_send - time.monotonic()
                    if sleep_for > 0:
                        time.sleep(sleep_for)
                    else:
                        # We're behind schedule; reset to avoid drift burst
                        next_send = time.monotonic()
        finally:
            self._emit(SenderEventType.PROGRESS, sent=sent, target=target_count or 0)
            if self._running.is_set():
                # Reached target naturally
                self._running.clear()
                self._emit(SenderEventType.STOPPED, f"Done ({sent} sent)")

    def _build_payload(self) -> bytes:
        if self.config.payload_template:
            # If size differs, pad or truncate to match
            t = self.config.payload_template
            if len(t) >= self.config.payload_size:
                return t[: self.config.payload_size]
            return t + b"\x00" * (self.config.payload_size - len(t))
        # Random payload with a 16-byte header for identification
        n = self.config.payload_size
        if n <= 0:
            return b""
        header = struct.pack("!dI", time.time(), random.randrange(1 << 32))
        body_len = max(0, n - len(header))
        # random.randbytes is Python 3.9+; fall back to getrandbits for 3.8
        # (the Win7 build runs on Python 3.8).
        if body_len:
            try:
                body = random.randbytes(body_len)
            except AttributeError:
                body = random.getrandbits(body_len * 8).to_bytes(body_len, "big")
        else:
            body = b""
        return header + body
