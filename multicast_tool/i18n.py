"""Minimal i18n for the multicast test tool.

Strings live in :data:`STRINGS` keyed by language code. Use :func:`t`
in widget code; use :func:`set_language` to switch at runtime and
call each tab's ``retranslate_ui()`` to apply.
"""

from __future__ import annotations

from typing import Callable

# Available languages. The first one is the default on first launch.
LANGUAGES: dict[str, str] = {
    "zh_CN": "中文",
    "en": "English",
}

# ---- Translation strings --------------------------------------------------- #

STRINGS: dict[str, dict[str, str]] = {
    "zh_CN": {
        # App
        "app.title": "组播测试工具 (IGMP / MLD)",
        # Tabs
        "tab.receive": "接收 / 统计",
        "tab.send": "发送",
        # Menus
        "menu.file": "文件(&F)",
        "menu.view": "视图(&V)",
        "menu.language": "语言(&L)",
        "menu.help": "帮助(&H)",
        "menu.lang.zh": "中文",
        "menu.lang.en": "English",
        "act.quit": "退出(&X)",
        "act.about": "关于(&A)",
        # Status bar
        "status.ready": "就绪",
        # Receive tab
        "recv.add_group": "添加组播组成员",
        "recv.add_family": "地址族:",
        "recv.add_group_addr": "组地址:",
        "recv.add_port": "端口:",
        "recv.add_iface": "接口:",
        "recv.add_version": "协议版本:",
        "recv.add_sources": "源 (用于 v3 SSM):",
        "recv.add_btn": "添加并开始",
        "recv.placeholder_group": "例如 224.0.0.1 或 ff02::1",
        "recv.placeholder_sources": "可选，逗号分隔 (用于 IGMPv3/MLDv2 SSM)",
        "recv.active": "活跃的组成员",
        "recv.btn_remove": "移除选中",
        "recv.btn_stop_all": "全部停止",
        "recv.btn_reset_counters": "重置计数 (选中)",
        "recv.btn_reset_columns": "重置列宽",
        "recv.log": "日志",
        "recv.remote_monitor": "发送端同步监控",
        "recv.remote_address": "发送端地址:",
        "recv.remote_address_ph": "host:port，例如 192.168.1.10:8765",
        "recv.remote_interval": "轮询间隔:",
        "recv.remote_unit_sec": "秒",
        "recv.remote_connect": "连接",
        "recv.remote_disconnect": "断开",
        "recv.remote_status": "状态:",
        "recv.remote_status_idle": "未连接",
        "recv.remote_status_connecting": "连接中...",
        "recv.remote_status_ok": "已连接",
        "recv.remote_status_err": "错误",
        "recv.remote_target": "目标:",
        "recv.remote_mode": "模式:",
        "recv.remote_elapsed": "已运行:",
        "recv.remote_sent": "发送包数:",
        "recv.remote_bytes": "发送字节:",
        "recv.remote_pps": "发送速率:",
        "recv.remote_bps": "发送位速率:",
        "recv.remote_no_sender": "等待发送端数据...",
        # Send tab
        "send.params": "发送参数",
        "send.family": "地址族:",
        "send.group": "组地址:",
        "send.port": "端口:",
        "send.iface": "出口接口:",
        "send.source": "源 IP:",
        "send.placeholder_source": "可选源 IP",
        "send.ttl": "TTL / 跳数限制:",
        "send.payload": "载荷大小 (字节):",
        "send.mode": "模式:",
        "send.count": "数量:",
        "send.rate": "速率:",
        "send.unit_pps": "pps",
        "send.template": "发送固定文本载荷 (ASCII)",
        "send.btn_start": "开始发送",
        "send.btn_stop": "停止",
        "send.progress": "进度:",
        "send.status": "状态:",
        "send.status_idle": "空闲",
        "send.expose": "暴露统计供远程监控",
        "send.expose_port": "监听端口:",
        "send.expose_start": "启动服务",
        "send.expose_stop": "停止服务",
        "send.expose_status_off": "未启动",
        "send.expose_status_on": "正在监听 {bind}:{port}",
        "send.expose_endpoint": "查询地址:",
        "send.expose_bind_net": "暴露到网络 (0.0.0.0；默认为仅本机 127.0.0.1)",
        "send.log": "发送日志",
        # Defaults / enums
        "iface.default": "(默认)",
        "mode.burst": "突发",
        "mode.rate": "限速",
        "mode.continuous": "连续",
        "family.ipv4": "IPv4",
        "family.ipv6": "IPv6",
        "ver.igmpv1": "IGMPv1",
        "ver.igmpv2": "IGMPv2",
        "ver.igmpv3": "IGMPv3",
        "ver.mldv1": "MLDv1",
        "ver.mldv2": "MLDv2",
        # Table headers
        "col.idx": "#",
        "col.group": "组地址",
        "col.port": "端口",
        "col.iface": "接口",
        "col.version": "版本",
        "col.sources": "源",
        "col.packets": "包数",
        "col.bytes": "字节",
        "col.rate_pps": "速率 (pps)",
        "col.rate_bps": "速率 (bps)",
        "col.elapsed": "已运行",
        # About
        "about.title": "关于组播测试工具",
        "about.body": (
            "<b>组播测试工具</b><br>"
            "IGMP/MLD 加入、IPv4/IPv6 组播发送、实时统计，"
            "并可将发送端速率同步到接收端统一显示。<br><br>"
            "基于 Python + PySide6，标准 socket API，"
            "无需 raw socket。"
        ),
        # Validation / dialog
        "dlg.invalid_input": "输入有误",
        "dlg.invalid_group": "无效的组地址",
        "dlg.failed_start": "启动失败",
        "dlg.cleared_state": "旧版列宽已重置为默认。",
    },
    "en": {
        "app.title": "Multicast Test Tool (IGMP / MLD)",
        "tab.receive": "Receive / Stats",
        "tab.send": "Send",
        "menu.file": "&File",
        "menu.view": "&View",
        "menu.language": "&Language",
        "menu.help": "&Help",
        "menu.lang.zh": "中文",
        "menu.lang.en": "English",
        "act.quit": "E&xit",
        "act.about": "&About",
        "status.ready": "Ready",
        "recv.add_group": "Add multicast group membership",
        "recv.add_family": "Address family:",
        "recv.add_group_addr": "Group address:",
        "recv.add_port": "Port:",
        "recv.add_iface": "Interface:",
        "recv.add_version": "Protocol version:",
        "recv.add_sources": "Sources (v3 SSM):",
        "recv.add_btn": "Add && Start",
        "recv.placeholder_group": "e.g. 224.0.0.1 or ff02::1",
        "recv.placeholder_sources": "optional, comma-separated (used with IGMPv3/MLDv2 SSM)",
        "recv.active": "Active memberships",
        "recv.btn_remove": "Remove Selected",
        "recv.btn_stop_all": "Stop All",
        "recv.btn_reset_counters": "Reset Counters (selected)",
        "recv.btn_reset_columns": "Reset Column Widths",
        "recv.log": "Log",
        "recv.remote_monitor": "Remote sender monitor",
        "recv.remote_address": "Sender address:",
        "recv.remote_address_ph": "host:port, e.g. 192.168.1.10:8765",
        "recv.remote_interval": "Poll interval:",
        "recv.remote_unit_sec": "s",
        "recv.remote_connect": "Connect",
        "recv.remote_disconnect": "Disconnect",
        "recv.remote_status": "Status:",
        "recv.remote_status_idle": "Idle",
        "recv.remote_status_connecting": "Connecting...",
        "recv.remote_status_ok": "Connected",
        "recv.remote_status_err": "Error",
        "recv.remote_target": "Target:",
        "recv.remote_mode": "Mode:",
        "recv.remote_elapsed": "Elapsed:",
        "recv.remote_sent": "Sent pkts:",
        "recv.remote_bytes": "Sent bytes:",
        "recv.remote_pps": "Send pps:",
        "recv.remote_bps": "Send bps:",
        "recv.remote_no_sender": "Waiting for sender data...",
        "send.params": "Send parameters",
        "send.family": "Address family:",
        "send.group": "Group address:",
        "send.port": "Port:",
        "send.iface": "Outgoing interface:",
        "send.source": "Source IP:",
        "send.placeholder_source": "optional source IP",
        "send.ttl": "TTL / Hop Limit:",
        "send.payload": "Payload size (bytes):",
        "send.mode": "Mode:",
        "send.count": "Count:",
        "send.rate": "Rate:",
        "send.unit_pps": "pps",
        "send.template": "Send fixed text payload (ASCII)",
        "send.btn_start": "Start Sending",
        "send.btn_stop": "Stop",
        "send.progress": "Progress:",
        "send.status": "Status:",
        "send.status_idle": "Idle",
        "send.expose": "Expose stats for remote monitoring",
        "send.expose_port": "Listen port:",
        "send.expose_start": "Start",
        "send.expose_stop": "Stop",
        "send.expose_status_off": "Not running",
        "send.expose_status_on": "Listening on 0.0.0.0:{port}",
        "send.expose_endpoint": "Query URL:",
        "send.log": "Send log",
        "iface.default": "(default)",
        "mode.burst": "burst",
        "mode.rate": "rate",
        "mode.continuous": "continuous",
        "family.ipv4": "IPv4",
        "family.ipv6": "IPv6",
        "ver.igmpv1": "IGMPv1",
        "ver.igmpv2": "IGMPv2",
        "ver.igmpv3": "IGMPv3",
        "ver.mldv1": "MLDv1",
        "ver.mldv2": "MLDv2",
        "col.idx": "#",
        "col.group": "Group",
        "col.port": "Port",
        "col.iface": "Interface",
        "col.version": "Version",
        "col.sources": "Sources",
        "col.packets": "Packets",
        "col.bytes": "Bytes",
        "col.rate_pps": "Rate (pps)",
        "col.rate_bps": "Rate (bps)",
        "col.elapsed": "Elapsed",
        "about.title": "About Multicast Test Tool",
        "about.body": (
            "<b>Multicast Test Tool</b><br>"
            "IGMP / MLD join, IPv4 / IPv6 multicast send, real-time statistics, "
            "with optional sender-rate sync to the receiver.<br><br>"
            "Built with Python + PySide6, standard socket APIs only -- no raw socket required."
        ),
        "dlg.invalid_input": "Invalid input",
        "dlg.invalid_group": "Invalid group address",
        "dlg.failed_start": "Failed to start",
        "dlg.cleared_state": "Old column state cleared; defaults restored.",
    },
}


# ---- Runtime state --------------------------------------------------------- #

_current_lang: str = "zh_CN"
_listeners: list[Callable[[str], None]] = []


def t(key: str) -> str:
    """Look up a translation key in the current language."""
    return STRINGS.get(_current_lang, STRINGS["zh_CN"]).get(key, key)


def set_language(lang: str) -> None:
    """Switch the current language. Unknown languages are ignored."""
    global _current_lang
    if lang in STRINGS:
        if _current_lang != lang:
            _current_lang = lang
            for cb in list(_listeners):
                try:
                    cb(lang)
                except Exception:  # noqa: BLE001
                    pass


def get_language() -> str:
    return _current_lang


def add_listener(cb: Callable[[str], None]) -> None:
    """Register a callback fired whenever the language changes."""
    if cb not in _listeners:
        _listeners.append(cb)


def remove_listener(cb: Callable[[str], None]) -> None:
    if cb in _listeners:
        _listeners.remove(cb)
