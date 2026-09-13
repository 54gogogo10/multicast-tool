# Multicast Test Tool (IGMP / MLD)

A cross-platform multicast testing tool written in Python + PySide6. It
lets you join IPv4 and IPv6 multicast groups, send multicast traffic, and
watch real-time receive statistics (packet count, byte count, pps, bps).

## Features

- **IGMP / MLD membership** — join and leave IPv4 and IPv6 multicast
  groups with selectable protocol version:
  - **IGMP**: v1, v2, v3
  - **MLD**: v1, v2
  - v3 / MLDv2 enable **source-specific multicast (SSM)**: provide a list
    of source IPs in the *Sources* field and the tool will issue
    `IP_ADD_SOURCE_MEMBERSHIP` / `IPV6_JOIN_SOURCE_GROUP` instead of the
    any-source join used by v1/v2.
- **Multiple concurrent receivers** — each group runs in its own thread;
  every row in the table shows live counters and a colour-coded rate
  (grey / green / amber / red).
- **Multicast sender** with three modes:
  - **Burst** — send N packets as fast as possible
  - **Rate-limited** — send N packets at a fixed pps
  - **Continuous** — send until you press Stop
  Configurable TTL / Hop Limit, payload size, source IP, and outgoing
  interface.
- **Real-time statistics** — 1-second sliding window pps / bps, plus
  cumulative packet and byte counts and per-row elapsed time.
- **Sender-rate synchronisation** — the sender tab can optionally expose
  its live stats (`sent` / `bytes` / `pps` / `bps` / `elapsed` / target)
  over a tiny HTTP server (`GET /stats`). The receiver tab can poll any
  sender's URL and display those numbers alongside its own receive
  stats, so a single dashboard shows both ends of the pipe.
- **Chinese / English UI** — the whole interface is translated; switch
  languages live via *View → Language → 中文 / English*.
- **Dark / light theme** — modern card-based interface with dashboard
  stat tiles; switch via *View → Theme → 深色 / 浅色* (persisted).
- **User-resizable columns** — all receive-table columns (including the
  Group address) can be dragged; widths persist across sessions.
- **Pure standard library + PySide6** — no raw sockets, no admin rights
  required on Windows or Linux for normal operation.

## Requirements

- Python 3.9 or newer (tested on 3.11)
- PySide6 6.6 or newer

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python run.py
```

## Project layout

```
multicast/
├── run.py                       # entry point
├── build.bat                    # one-click PyInstaller build (onedir)
├── multicast_tool.spec          # PyInstaller spec
├── requirements.txt
├── README.md
└── multicast_tool/
    ├── __init__.py
    ├── core.py                  # MulticastReceiver, MulticastSender, helpers
    ├── stats.py                 # StatsTracker, format helpers
    ├── remote.py                # StatsExporter, RemoteSenderPoller
    ├── i18n.py                  # zh_CN / en translation strings
    ├── theme.py                 # dark/light palettes + QSS design system
    └── ui.py                    # MainWindow, ReceiveTab, SendTab
```

## Build a Windows executable

The default build uses **PySide6 (Qt 6)** and runs on Windows 10 / 11.

```cmd
build.bat                REM default: PySide6, onedir, dist\MulticastTool\
build.bat onefile        REM PySide6, single-file, dist\MulticastTool.exe
```

For **Windows 7** (and Server 2012 / 2012 R2), build the PySide2 / Qt 5
variant. PySide6 requires Windows 10+, so a separate toolchain is used:

```cmd
build.bat win7          REM PySide2, onedir, dist\MulticastTool-Win7\
build.bat onefile-win7  REM PySide2, single-file, dist\MulticastTool-Win7.exe
```

The `win7` mode requires a separate virtual environment at
`venv-win7` with PySide2 5.15 and PyInstaller 5.x (see *Windows 7
build* below). The source code is shared — a small shim in
`multicast_tool/qt_compat.py` picks PySide2 at import time when PySide6
isn't present, so the same `.py` files build either variant.

You can also invoke PyInstaller directly:

```cmd
pyinstaller --noconfirm --clean multicast_tool.spec         REM Win10+
pyinstaller --noconfirm --clean multicast_tool.win7.spec   REM Win7
```

### All four build artifacts

| Target | Format | Path | Size | First-start latency |
|---|---|---|---|---|
| Win10+ | onedir  | `dist\MulticastTool\MulticastTool.exe` + `_internal\` | 4 MB + 619 MB | ~1 s |
| Win10+ | onefile | `dist\MulticastTool.exe` | 41 MB | ~3 s |
| **Win7**  | onedir  | `dist\MulticastTool-Win7\MulticastTool-Win7.exe` + `_internal\` | 1 MB + 313 MB | ~1 s |
| **Win7**  | onefile | `dist\MulticastTool-Win7.exe` | 33 MB | ~3 s |

**Onedir** = fast startup, easier to debug, ships as a folder.
**Onefile** = single .exe to copy around, slower first start because
PyInstaller extracts the bundle to `%TEMP%` on each launch.

Both variants are fully self-contained: the Python runtime, Qt
libraries (6 or 5), and all dependencies are bundled inside. The
target machine does **not** need Python, PySide, Qt, or any other
package pre-installed.

Sanity-check the bundled exes in a headless environment:

```bash
python _exe_smoke.py        REM onedir build
python _onefile_smoke.py    REM onefile builds
```

## Windows 7 deployment

The Win7 exe is a regular Win32 / Win64 PE binary. Copy the `onedir`
folder (or the single onefile .exe) to the target machine and double-
click. There is no installer, no DLL to register, and no Python to set
up.

### Required OS patches

Win7 is end-of-life and needs two updates that Win10+ already include.
Without them, the exe fails to start with a missing-DLL error:

| Update | Why | When needed |
|---|---|---|
| **Windows 7 Service Pack 1** | baseline for all modern runtimes | always |
| **KB2999226** (Universal C Runtime) | Python 3.5+ links against UCRT forwarders (`api-ms-win-crt-*.dll`); these are part of the OS, not redistributable as separate DLLs | always for any Python 3.5+ binary, including ours |
| **KB2533623** (optional) | enables TLS 1.2 in WinHTTP; only needed for HTTPS-based remote sender polling | only if you use HTTPS URLs |

The first two are usually already installed on any reasonably-patched
Win7 box. To verify, run `cmd` as Administrator:

```cmd
wmic qfe list | find "KB2999226"
```

If it returns nothing, fetch the update from Microsoft and install
(requires a reboot). For a fully offline target you can stage the
`.msu` next to the exe.

### Verifying the bundle on Win7

1. Copy `dist\MulticastTool-Win7\` (or the onefile .exe) to the Win7
   machine.
2. Open `cmd` and run the exe from there:
   ```cmd
   cd C:\Users\you\Desktop\MulticastTool-Win7
   MulticastTool-Win7.exe
   ```
3. If the window appears, the bundle is healthy. If you get
   `api-ms-win-crt-*.dll is missing`, install KB2999226 (see above).

## Windows 7 build (development machine)

The Win7 build needs Python 3.8 (the highest version PySide2 5.15
officially supports) and a slimmed-down PyInstaller 5.x. The repo ships
a pre-isolated `venv-win7` (created from `D:\software\pyenv\pyenv-win\
versions\3.8.10\python.exe`) and offline-downloaded wheels in
`wheels-win7\` so the build does not need network access.

To (re)create the environment from scratch on a machine with `pyenv-win`:

```cmd
set PY38=C:\path\to\python3.8\python.exe
%PY38% -m venv venv-win7
REM Download wheels for offline use (handles the corporate SSL chain)
python -m pip download "PySide2==5.15.2.1" "pyinstaller==5.13.2" ^
    "importlib-metadata" "pyinstaller-hooks-contrib" ^
    --python-version 38 --platform win_amd64 --only-binary=:all: ^
    -d wheels-win7
venv-win7\Scripts\python.exe -m pip install --no-index ^
    --find-links wheels-win7 PySide2 "pyinstaller==5.13.2"
```

Then build the Win7 exe:

```cmd
build.bat win7
```

Output: `dist\MulticastTool-Win7\MulticastTool-Win7.exe` (about 1.2 MB)
plus `_internal\` (about 313 MB including PySide2 / Qt 5 DLLs). It runs
on Windows 7 SP1, Server 2008 R2 and Server 2012 without any
additional runtime install.

## How protocol version selection works

| Family | UI choice     | Socket call                              | Notes                        |
| ------ | ------------- | ---------------------------------------- | ---------------------------- |
| IPv4   | IGMPv1 / v2   | `IP_ADD_MEMBERSHIP`                      | Any-source (ASM) join        |
| IPv4   | IGMPv3        | `IP_ADD_MEMBERSHIP`                      | ASM if no sources given      |
| IPv4   | IGMPv3 + src  | `IP_ADD_SOURCE_MEMBERSHIP` per source    | SSM (IGMPv3 semantics)       |
| IPv6   | MLDv1         | `IPV6_JOIN_GROUP`                        | Any-source (ASM) join        |
| IPv6   | MLDv2         | `IPV6_JOIN_GROUP`                        | ASM if no sources given      |
| IPv6   | MLDv2 + src   | `IPV6_JOIN_SOURCE_GROUP` per source      | SSM (MLDv2 semantics)        |

The actual IGMP/MLD message type on the wire is decided by the network
(querier) and the OS. The choices above tell the kernel which kind of
membership report to install; the kernel translates that into the
appropriate protocol version per RFC.

## Sender-rate synchronisation

To see the sender's live rate from the receiver side:

1. On the **sender** machine, open the *Send* tab and scroll to
   *Expose stats for remote monitoring*. Pick a port (default `8765`)
   and click *Start*. The exporter binds to `0.0.0.0:port` and serves
   `GET /stats` as JSON.
2. On the **receiver** machine (could be the same host, different host
   or container), open the *Receive / Stats* tab and fill in the
   *Remote sender monitor*:
   - **Sender address**: `host:port`, e.g. `192.168.1.10:8765`
   - **Poll interval**: 0.2 – 60 s
   - Click *Connect*. The panel will start showing the sender's
     `sent` / `bytes` / `pps` / `bps` / `elapsed` and target group.

The HTTP protocol is trivially scrapable with `curl`:

```bash
curl http://192.168.1.10:8765/stats
# {"status":"running","sent":12345,"bytes":6789012,"pps":100.5,
#  "bps":824000,"elapsed_sec":60.2,"target_group":"239.1.1.1",
#  "target_port":5000,"target_family":"IPv4","mode":"burst",
#  "target_count":1000}
```

## Self-test

Five headless test scripts cover different layers:

```bash
python _selftest.py      # core / stats / multicast roundtrip
python _uitest.py        # window opens and closes cleanly
python _colwidth_test.py # receive-table column resize + persistence
python _remote_test.py   # sender-rate sync end-to-end
python _i18n_test.py     # language switcher
```

## Tips

- **FRR 真机验证**: FRR 10.x 上需在接口同时配置 `ip pim sm` **和**
  `ip igmp`（vtysh）。只配 PIM 时 pimd 只创建一个 mtrace-only socket，
  会**静默丢弃**所有成员报告，`show ip igmp groups` 恒为空 —— 用
  `show ip igmp interface <if> json` 的 `mtraceOnly` 字段可确认。
  IPv6 同理：`ipv6 pim sm` 之外还需 `ipv6 mld`。
- **两台 FRR 的测试拓扑**（无需第二台机器）: 用 `ip netns` + veth 构造
  第二台 FRR，`systemctl start frr@<pathspace>`（配置放
  `/etc/frr/<pathspace>/`，含 `daemons` 文件）。坑：Ubuntu 的 AppArmor
  会按守护进程限制 pathspace 子目录的 pid 文件写入（zebra 可以、
  pimd/pim6d 被拒），需卸载对应 profile：
  `echo -n pimd > /sys/kernel/security/apparmor/.remove`。
  另外两平台的 IPv6 SSM socket API 只有**选项号**不同：Winsock 用
  `MCAST_JOIN_SOURCE_GROUP (45) / MCAST_LEAVE_SOURCE_GROUP (46)`，
  Linux 用 46 / 47；payload 布局两平台**一致**（RFC 3678
  group_source_req：u32 ifindex 在前，后接 8 字节对齐的
  group / source SOCKADDR_STORAGE——已对照 ws2ipdef.h 与 linux uapi in.h
  核实；注意 IPv4 的 `ip_mreq_source` 才是两平台字段顺序真正不同的）。
- **Loopback testing**: pick a group in `239.0.0.0/8` (administratively
  scoped) and use TTL = 0 in the sender; this confines traffic to the
  host and is great for sanity-checking the tool.
- **Multiple receivers on the same port**: the tool enables
  `SO_REUSEADDR` (and `SO_REUSEPORT` on Linux) so two receivers can
  share a port on the same machine.
- **No traffic showing up?**
  - Make sure the host's firewall allows UDP on the port.
  - On Windows, check that the network profile isn't set to *Public*
    with blocking rules.
  - Some routers drop multicast by default on Wi-Fi. Try a wired
    interface or a different SSID.

## License

MIT
