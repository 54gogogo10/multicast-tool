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
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .stats import StatsTracker

logger = logging.getLogger(__name__)

# RFC 3678 SSM socket options. Windows' socket module does not export the
# source-membership constants at all (Linux does), so fall back to the raw
# Winsock / POSIX values: ws2ipdef.h defines IP_ADD_SOURCE_MEMBERSHIP=15,
# IP_DROP_SOURCE_MEMBERSHIP=16, IPV6_JOIN_SOURCE_GROUP=30,
# IPV6_LEAVE_SOURCE_GROUP=31. The ip_mreq_source / ipv6_mreq_source
# layouts are identical on both platforms.
IP_ADD_SOURCE_MEMBERSHIP = getattr(socket, "IP_ADD_SOURCE_MEMBERSHIP", 15)
IP_DROP_SOURCE_MEMBERSHIP = getattr(socket, "IP_DROP_SOURCE_MEMBERSHIP", 16)
IPV6_JOIN_GROUP = getattr(socket, "IPV6_JOIN_GROUP", 12)
IPV6_LEAVE_GROUP = getattr(socket, "IPV6_LEAVE_GROUP", 13)


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
    Accepts interface names (e.g. 'eth0', 'Wi-Fi', Windows display names)
    and local interface IPs. Returns 0 when the input cannot be resolved;
    callers that require an explicit interface treat 0 as an error.
    """
    s = (name_or_ip or "").strip()
    if not s or s.lower() in ("auto", "default", "*", "0"):
        return 0
    # 1) Direct name lookup (handles 'eth0', 'Wi-Fi', ...)
    try:
        idx = socket.if_nametoindex(s)
        if idx:
            return idx
    except (OSError, AttributeError):
        pass
    # 2) Maybe the user passed an IP or a display name; consult the table
    ip_to_index, _name_to_ipv4, name_to_index = _iface_tables()[
        0 if family is AddressFamily.IPV4 else 1
    ]
    return ip_to_index.get(s) or name_to_index.get(s) or 0


def _resolve_ipv4_interface_address(spec: str) -> bytes:
    """Resolve an interface name or IP to a 4-byte IPv4 address.

    Used for ``ip_mreq``-style socket options that need an interface
    *address* rather than an index. Raises ``ValueError`` (instead of
    silently falling back to the default interface) when the spec cannot
    be resolved.
    """
    s = spec.strip()
    try:
        addr = ipaddress.ip_address(s)
    except ValueError:
        addr = None
    if isinstance(addr, ipaddress.IPv4Address):
        return socket.inet_aton(s)
    if addr is not None:
        raise ValueError(f"interface {s!r} is not an IPv4 address")
    ip = _iface_tables()[0][1].get(s)
    if ip is None:
        raise ValueError(
            f"cannot resolve interface {s!r} to an IPv4 address; "
            f"leave the interface empty for the system default"
        )
    return socket.inet_aton(ip)


def _sa6_storage(addr_bin: bytes, scope: int = 0) -> bytes:
    """sockaddr_in6 padded into a SOCKADDR_STORAGE-sized (128 byte) blob."""
    sa = struct.pack("=HHI16sI", socket.AF_INET6, 0, 0, addr_bin, scope)
    return sa + b"\x00" * (128 - len(sa))


def _af(family: AddressFamily) -> int:
    return socket.AF_INET if family is AddressFamily.IPV4 else socket.AF_INET6


def _win_default_ipv6_ifindex() -> int:
    """Best-effort default IPv6 interface index for Windows SSM joins.

    Winsock's ``IPV6_JOIN_SOURCE_GROUP`` rejects interface index 0 with
    WSAEINVAL (unlike the IPv4 option, which accepts INADDR_ANY), so a
    real interface is required. Prefer one with a global (non-link-local)
    unicast address; fall back to any enumerated interface.
    """
    ip_to_index = _iface_tables()[1][0]
    fallback = 0
    for ip, index in ip_to_index.items():
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not isinstance(addr, ipaddress.IPv6Address) or not index:
            continue
        if not (addr.is_link_local or addr.is_loopback or addr.is_multicast):
            return index
        if not fallback:
            fallback = index
    return fallback


# --------------------------------------------------------------------------- #
# Interface table (IP / name -> index / address)                              #
# --------------------------------------------------------------------------- #

# Per-family interface table: (ip_to_index, name_to_ipv4, name_to_index)
#   ip_to_index:   local IP string -> interface index (for IPv6 socket options)
#   name_to_ipv4:  interface display name -> first IPv4 address (for ip_mreq)
#   name_to_index: interface display name -> interface index
_IFACE_TABLES_LOCK = threading.Lock()
_IFACE_TABLES: Optional[tuple[tuple[dict, dict, dict], tuple[dict, dict, dict]]] = None


def _iface_tables() -> tuple[tuple[dict, dict, dict], tuple[dict, dict, dict]]:
    """Return cached (IPv4, IPv6) interface tables.

    The tables map local IPs and interface display names to the numeric
    indexes and IPv4 addresses that multicast socket options need. They
    are built once per process; on platforms without a known enumeration
    method both tables are empty and callers fall back to the kernel
    default interface.
    """
    global _IFACE_TABLES
    with _IFACE_TABLES_LOCK:
        if _IFACE_TABLES is None:
            if sys.platform.startswith("win"):
                _IFACE_TABLES = _win_iface_tables()
            elif sys.platform.startswith("linux"):
                _IFACE_TABLES = _linux_iface_tables()
            else:
                _IFACE_TABLES = (({}, {}, {}), ({}, {}, {}))
        return _IFACE_TABLES


def _win_iface_tables() -> tuple[tuple[dict, dict, dict], tuple[dict, dict, dict]]:
    """Windows: enumerate adapters via GetAdaptersAddresses (ctypes)."""
    v4: tuple[dict, dict, dict] = ({}, {}, {})
    v6: tuple[dict, dict, dict] = ({}, {}, {})
    try:
        import ctypes
        from ctypes import wintypes

        class _SockAddr(ctypes.Structure):
            _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]

        class _Uca(ctypes.Structure):
            pass

        class _Aaa(ctypes.Structure):
            pass

        _Uca._fields_ = [
            ("Length", wintypes.ULONG),
            ("Flags", wintypes.DWORD),
            ("Next", ctypes.POINTER(_Uca)),
            ("Address", _SockAddr),
            ("PrefixOrigin", wintypes.DWORD),
            ("SuffixOrigin", wintypes.DWORD),
            ("DadState", wintypes.DWORD),
            ("ValidLifetime", wintypes.ULONG),
            ("PreferredLifetime", wintypes.ULONG),
            ("LeaseLifetime", wintypes.ULONG),
            ("OnLinkPrefixLength", ctypes.c_ubyte),
        ]
        _Aaa._fields_ = [
            ("Length", wintypes.ULONG),
            ("IfIndex", wintypes.DWORD),
            ("Next", ctypes.POINTER(_Aaa)),
            ("AdapterName", ctypes.c_char_p),
            ("FirstUnicastAddress", ctypes.POINTER(_Uca)),
            ("FirstAnycastAddress", ctypes.c_void_p),
            ("FirstMulticastAddress", ctypes.c_void_p),
            ("FirstDnsServerAddress", ctypes.c_void_p),
            ("DnsSuffix", ctypes.c_wchar_p),
            ("Description", ctypes.c_wchar_p),
            ("FriendlyName", ctypes.c_wchar_p),
            ("PhysicalAddress", ctypes.c_ubyte * 8),
            ("PhysicalAddressLength", wintypes.ULONG),
            ("Flags", wintypes.DWORD),
            ("Mtu", wintypes.DWORD),
            ("IfType", wintypes.DWORD),
            ("OperStatus", wintypes.DWORD),
            ("Ipv6IfIndex", wintypes.DWORD),
            ("ZoneIndices", wintypes.DWORD * 16),
            ("FirstPrefix", ctypes.c_void_p),
        ]

        get_adapters = ctypes.windll.iphlpapi.GetAdaptersAddresses
        get_adapters.restype = wintypes.ULONG
        get_adapters.argtypes = [
            wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p,
            ctypes.POINTER(_Aaa), ctypes.POINTER(wintypes.ULONG),
        ]
        # Skip anycast / multicast / DNS addresses; unicast is all we need.
        flags = 0x2 | 0x4 | 0x8
        buf_size = wintypes.ULONG(16 * 1024)
        buf = None
        rc = -1
        for _ in range(4):
            buf = ctypes.create_string_buffer(buf_size.value)
            rc = get_adapters(
                0, flags, None,
                ctypes.cast(buf, ctypes.POINTER(_Aaa)), ctypes.byref(buf_size),
            )
            if rc == 0:  # NO_ERROR
                break
            if rc != 111:  # ERROR_BUFFER_OVERFLOW
                return v4, v6
        if rc != 0 or buf is None:
            return v4, v6

        idx_to_ipv4: dict[int, str] = {}
        p = ctypes.cast(buf, ctypes.POINTER(_Aaa))
        while p:
            a = p.contents
            name = a.FriendlyName or ""
            idx_v4, idx_v6 = int(a.IfIndex), int(a.Ipv6IfIndex)
            ua = a.FirstUnicastAddress
            while ua:
                u = ua.contents
                sa = u.Address.lpSockaddr
                if sa:
                    fam = ctypes.c_ushort.from_address(sa).value
                    if fam == socket.AF_INET:
                        ip = socket.inet_ntop(socket.AF_INET, ctypes.string_at(sa + 4, 4))
                        if idx_v4:
                            v4[0][ip] = idx_v4
                            idx_to_ipv4[idx_v4] = ip
                        if name:
                            v4[1].setdefault(name, ip)
                    elif fam == socket.AF_INET6:
                        ip = socket.inet_ntop(socket.AF_INET6, ctypes.string_at(sa + 8, 16))
                        if idx_v6:
                            v6[0][ip] = idx_v6
                        if name:
                            v6[2].setdefault(name, idx_v6)
                ua = u.Next
            if name:
                if idx_v4:
                    v4[2].setdefault(name, idx_v4)
            p = a.Next

        # Also accept the names socket.if_nameindex() reports (they differ
        # from the display names on some Windows versions): map them via
        # the shared index space.
        try:
            for cindex, cname in socket.if_nameindex():
                try:
                    cidx = socket.if_nametoindex(cname)
                except OSError:
                    continue
                if cname not in v4[1] and cidx in idx_to_ipv4:
                    v4[1][cname] = idx_to_ipv4[cidx]
        except (OSError, AttributeError, TypeError):
            pass
    except Exception:  # noqa: BLE001 - never let enumeration break the app
        logger.debug("GetAdaptersAddresses enumeration failed", exc_info=True)
    return v4, v6


def _linux_iface_tables() -> tuple[tuple[dict, dict, dict], tuple[dict, dict, dict]]:
    """Linux: /proc/net/if_inet6 for IPv6, SIOCGIFADDR ioctl for IPv4."""
    v4: tuple[dict, dict, dict] = ({}, {}, {})
    v6: tuple[dict, dict, dict] = ({}, {}, {})
    # IPv6: '<addr-hex> <ifindex-hex> <prefixlen> <scope> <flags> <name>'
    try:
        with open("/proc/net/if_inet6", encoding="ascii") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 6:
                    continue
                try:
                    addr = ipaddress.IPv6Address(int(parts[0], 16))
                    idx = int(parts[1], 16)
                except ValueError:
                    continue
                if idx:
                    v6[0][str(addr)] = idx
                    v6[2].setdefault(parts[5], idx)
    except OSError:
        pass
    # IPv4: SIOCGIFADDR per interface name
    try:
        import fcntl

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for idx, name in socket.if_nameindex():
                try:
                    packed = fcntl.ioctl(
                        s.fileno(), 0x8915, struct.pack("256s", name[:15].encode())
                    )
                except OSError:
                    continue
                ip = socket.inet_ntoa(packed[20:24])
                v4[0][ip] = idx
                v4[1].setdefault(name, ip)
                v4[2].setdefault(name, idx)
        finally:
            s.close()
    except (OSError, ImportError):
        pass
    return v4, v6


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
        # Validate source list (will raise on bad input). Sources must also
        # match the address family of the group.
        for s in self.config.sources:
            try:
                addr = ipaddress.ip_address(s)
            except ValueError:
                raise ValueError(f"Invalid source IP: {s!r}") from None
            if self.config.family is AddressFamily.IPV4 and not isinstance(
                addr, ipaddress.IPv4Address
            ):
                raise ValueError(f"Source {s!r} is not IPv4")
            if self.config.family is AddressFamily.IPV6 and not isinstance(
                addr, ipaddress.IPv6Address
            ):
                raise ValueError(f"Source {s!r} is not IPv6")

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
        if iface and iface.lower() not in ("auto", "default", "*", "0"):
            ifaddr = _resolve_ipv4_interface_address(iface)
        else:
            ifaddr = socket.inet_aton("0.0.0.0")
        if self.config.igmp_version is IgmpVersion.V3 and self.config.sources:
            # IP_ADD_SOURCE_MEMBERSHIP (IGMPv3 SSM). NOTE: the two platforms
            # define struct ip_mreq_source with DIFFERENT field order:
            #   Linux  ip_mreq_source:  multiaddr, interface, sourceaddr
            #   Win32  ip_mreq_source:  multiaddr, sourceaddr, interface
            for src in self.config.sources:
                src_bin = socket.inet_aton(src)
                if sys.platform.startswith("win"):
                    mreq = struct.pack("!4s4s4s", group_bin, src_bin, ifaddr)
                else:
                    mreq = struct.pack("!4s4s4s", group_bin, ifaddr, src_bin)
                sock.setsockopt(socket.IPPROTO_IP, IP_ADD_SOURCE_MEMBERSHIP, mreq)
        else:
            mreq = struct.pack("!4s4s", group_bin, ifaddr)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    def _leave_ipv4(self, sock: socket.socket) -> None:
        group_bin = socket.inet_aton(self.config.group)
        iface = self.config.interface.strip()
        if iface and iface.lower() not in ("auto", "default", "*", "0"):
            try:
                ifaddr = _resolve_ipv4_interface_address(iface)
            except ValueError:
                ifaddr = socket.inet_aton("0.0.0.0")
        else:
            ifaddr = socket.inet_aton("0.0.0.0")
        if self.config.igmp_version is IgmpVersion.V3 and self.config.sources:
            for src in self.config.sources:
                src_bin = socket.inet_aton(src)
                if sys.platform.startswith("win"):
                    mreq = struct.pack("!4s4s4s", group_bin, src_bin, ifaddr)
                else:
                    mreq = struct.pack("!4s4s4s", group_bin, ifaddr, src_bin)
                try:
                    sock.setsockopt(socket.IPPROTO_IP, IP_DROP_SOURCE_MEMBERSHIP, mreq)
                except OSError:
                    pass
        else:
            mreq = struct.pack("!4s4s", group_bin, ifaddr)
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
            except OSError:
                pass

    def _join_ipv6(self, sock: socket.socket) -> None:
        # Resolve the interface to a numeric index (name, IP, or default).
        # An explicitly requested interface that cannot be resolved is an
        # error: silently joining on the default interface would be worse.
        iface = self.config.interface.strip()
        if iface and iface.lower() not in ("auto", "default", "*", "0"):
            idx = interface_index(iface, self.config.family)
            if not idx:
                raise ValueError(
                    f"cannot resolve interface {iface!r} to an interface index; "
                    f"leave the interface empty for the system default"
                )
        else:
            idx = 0
        if sys.platform.startswith("win") and idx == 0:
            # Winsock IPV6_JOIN_SOURCE_GROUP requires a real interface index.
            idx = _win_default_ipv6_ifindex()
            if not idx:
                raise ValueError(
                    "no IPv6 interface found; pick one explicitly in the "
                    "interface field"
                )
        group_bin = socket.inet_pton(socket.AF_INET6, self.config.group)
        if self.config.mld_version is MldVersion.V2 and self.config.sources:
            # MLDv2 SSM. The option numbers differ between platforms, but
            # the payload layout is IDENTICAL (RFC 3678 group_source_req,
            # confirmed against ws2ipdef.h / linux uapi in.h):
            #   { u32 ifindex, sockaddr_storage group, sockaddr_storage source }
            # with 8-byte alignment for the storage members (4 pad bytes).
            #   Winsock: MCAST_JOIN_SOURCE_GROUP (45) at IPPROTO_IPV6
            #   Linux:   MCAST_JOIN_SOURCE_GROUP (46)
            for src in self.config.sources:
                src_bin = socket.inet_pton(socket.AF_INET6, src)
                if sys.platform.startswith("win"):
                    opt = 45  # MCAST_JOIN_SOURCE_GROUP (Winsock)
                else:
                    opt = 46  # MCAST_JOIN_SOURCE_GROUP (Linux)
                gsr = (struct.pack("I", idx) + b"\x00" * 4
                       + _sa6_storage(group_bin)
                       + _sa6_storage(src_bin))
                sock.setsockopt(socket.IPPROTO_IPV6, opt, gsr)
        else:
            mreq = group_bin + struct.pack("I", idx)
            sock.setsockopt(socket.IPPROTO_IPV6, IPV6_JOIN_GROUP, mreq)

    def _leave_ipv6(self, sock: socket.socket) -> None:
        iface = self.config.interface.strip()
        idx = interface_index(iface, self.config.family) if iface else 0
        if sys.platform.startswith("win") and idx == 0:
            idx = _win_default_ipv6_ifindex()
        group_bin = socket.inet_pton(socket.AF_INET6, self.config.group)
        if self.config.mld_version is MldVersion.V2 and self.config.sources:
            for src in self.config.sources:
                src_bin = socket.inet_pton(socket.AF_INET6, src)
                try:
                    if sys.platform.startswith("win"):
                        opt = 46  # MCAST_LEAVE_SOURCE_GROUP (Winsock)
                    else:
                        opt = 47  # MCAST_LEAVE_SOURCE_GROUP (Linux)
                    # Same layout as the join above (RFC 3678 group_source_req).
                    gsr = (struct.pack("I", idx) + b"\x00" * 4
                           + _sa6_storage(group_bin)
                           + _sa6_storage(src_bin))
                    sock.setsockopt(socket.IPPROTO_IPV6, opt, gsr)
                except OSError:
                    pass
        else:
            mreq = group_bin + struct.pack("I", idx)
            try:
                sock.setsockopt(socket.IPPROTO_IPV6, IPV6_LEAVE_GROUP, mreq)
            except OSError:
                pass

    def _recv_loop(self) -> None:
        assert self._sock is not None
        sock = self._sock
        while self._running.is_set():
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as e:
                if not self._running.is_set():
                    break
                # A real receive error: mark the receiver stopped, close the
                # socket and surface the error. is_running() must not stay
                # True while the thread is gone.
                self._error = str(e)
                self._running.clear()
                self._joined = False
                self._sock = None
                try:
                    sock.close()
                except OSError:
                    pass
                self._emit(ReceiverEventType.ERROR, f"recv error: {e}")
                break
            self.stats.record(len(data))


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
            # Set outgoing interface (IP_MULTICAST_IF wants an address)
            if self.config.source:
                ifaddr = _resolve_ipv4_interface_address(self.config.source)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, ifaddr)
            elif self.config.interface:
                iface = self.config.interface.strip()
                if iface.lower() not in ("auto", "default", "*", "0"):
                    ifaddr = _resolve_ipv4_interface_address(iface)
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, ifaddr)
            # TTL
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.config.ttl)
            # Loopback enabled by default; leave as-is
        else:  # IPv6
            if self.config.source or self.config.interface:
                # Convert to interface index; unresolvable specs are an error
                # (previously they were silently ignored).
                spec = (self.config.source or self.config.interface).strip()
                if spec.lower() not in ("auto", "default", "*", "0"):
                    idx = interface_index(spec, self.config.family)
                    if not idx:
                        raise ValueError(
                            f"cannot resolve interface {spec!r} to an interface index; "
                            f"leave the interface empty for the system default"
                        )
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
        last_progress_at = 0.0
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
                now = time.monotonic()
                # Throttle progress events: at most one per 100 ms, so a
                # high-rate continuous send cannot flood the GUI thread.
                if (sent - last_progress_sent >= progress_step
                        and now - last_progress_at >= 0.1):
                    last_progress_sent = sent
                    last_progress_at = now
                    self._emit(
                        SenderEventType.PROGRESS, sent=sent, target=target_count or 0
                    )
                if interval > 0:
                    next_send += interval
                    sleep_for = next_send - time.monotonic()
                    if sleep_for > 0:
                        # NB: single sleep() call. Slicing the wait (or using
                        # Event.wait) makes Windows sleep a full 15.6 ms timer
                        # quantum per call, which destroys pacing accuracy.
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
