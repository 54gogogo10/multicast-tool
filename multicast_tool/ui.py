"""PySide6 main window for the multicast test tool.

Two tabs:

* **Receive / Stats** -- join / leave IGMP / MLD groups, see live
  receive statistics, and (optionally) poll a remote sender to mirror
  its send rate in this tab.
* **Send** -- send IPv4 / IPv6 multicast traffic in burst, rate-limited
  or continuous mode. Optionally exposes the sender's live stats over
  a tiny HTTP server so another instance can display them.

All user-facing strings are routed through :mod:`multicast_tool.i18n`
so the UI can be flipped between Chinese and English at runtime.
"""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

# Qt binding shim: PySide6 (default, Windows 10+) or PySide2 (Win7 build).
from .qt_compat import (  # noqa: F401
    QtCore, QtGui, QtWidgets,
    Qt, QCoreApplication, QTimer, QByteArray, QEvent, QSettings, Signal, QObject,
    QAction, QColor, QFont, QKeySequence,
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QStatusBar, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
    PYSIDE_VERSION,
)

from . import i18n
from .core import (
    AddressFamily,
    IgmpVersion,
    MldVersion,
    MulticastReceiver,
    ReceiverConfig,
    ReceiverEvent,
    ReceiverEventType,
    SenderConfig,
    SenderEvent,
    SenderEventType,
    SenderMode,
    MulticastSender,
    is_multicast_group,
    local_addresses,
    parse_source_list,
)
from .remote import (
    RemoteSenderPoller,
    RemoteSenderSnapshot,
    StatsExporter,
)
from .stats import StatsSnapshot, StatsTracker, format_bytes, format_rate_bps

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Signal bridge                                                               #
# --------------------------------------------------------------------------- #


class _SignalBus(QObject):
    receiver_event = Signal(int, object)   # (row, ReceiverEvent)
    sender_event = Signal(object)          # SenderEvent


# --------------------------------------------------------------------------- #
# App state                                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class ReceiverRow:
    row_id: int
    receiver: MulticastReceiver
    stats: StatsTracker
    config: ReceiverConfig
    started_at: float


@dataclass
class AppState:
    receivers: dict[int, ReceiverRow] = field(default_factory=dict)
    next_row_id: int = 1
    sender: Optional[MulticastSender] = None
    sender_stats: Optional[StatsTracker] = None
    # Remote-monitor (sender -> HTTP -> this receiver's UI)
    remote_poller: Optional[RemoteSenderPoller] = None
    # Stats-exporter (this sender side)
    stats_exporter: Optional[StatsExporter] = None


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _color_for_pps(pps: float) -> QColor:
    if pps <= 0.5:
        return QColor("#888888")
    if pps < 50:
        return QColor("#1f8a3a")
    if pps < 1000:
        return QColor("#cf8a00")
    return QColor("#c0392b")


def _format_elapsed(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fill_combo(combo: QComboBox, items: list[str], current: Optional[str] = None) -> None:
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(items)
    if current is not None and current in items:
        combo.setCurrentText(current)
    combo.blockSignals(False)


# --------------------------------------------------------------------------- #
# Receive tab                                                                 #
# --------------------------------------------------------------------------- #


class ReceiveTab(QWidget):
    """UI for adding, monitoring and removing multicast group memberships,
    plus a 'Remote sender monitor' panel that polls a remote sender and
    displays its send rate alongside the receive stats."""

    HEADER_KEYS = [
        "col.idx", "col.group", "col.port", "col.iface", "col.version",
        "col.sources", "col.packets", "col.bytes", "col.rate_pps",
        "col.rate_bps", "col.elapsed",
    ]

    def __init__(self, state: AppState, bus: _SignalBus, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.bus = bus
        # Cached widget refs that need to be re-translated
        self._widgets: dict[str, QWidget] = {}
        self._build_ui()
        self._wire()

    # -- build ------------------------------------------------------------- #

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Add form ----------------------------------------------------------
        add_box = QGroupBox()
        self._widgets["recv.add_group"] = add_box
        form = QFormLayout(add_box)

        self.family_combo = QComboBox()
        self.iface_combo = QComboBox()
        self.iface_combo.setEditable(True)
        self.version_combo = QComboBox()
        self.group_edit = QLineEdit("224.0.0.1")
        self.port_edit = QSpinBox(); self.port_edit.setRange(1, 65535); self.port_edit.setValue(5000)
        self.sources_edit = QLineEdit()
        self.add_btn = QPushButton()
        self.add_btn.setDefault(True)

        self._widgets["recv.add_family"] = self._label_for(form, self.family_combo)
        self._widgets["recv.add_group_addr"] = self._label_for(form, self.group_edit)
        self._widgets["recv.add_port"] = self._label_for(form, self.port_edit)
        self._widgets["recv.add_iface"] = self._label_for(form, self.iface_combo)
        self._widgets["recv.add_version"] = self._label_for(form, self.version_combo)
        self._widgets["recv.add_sources"] = self._label_for(form, self.sources_edit)
        self._widgets["recv.add_btn"] = self.add_btn

        form.addRow(self._widgets["recv.add_family"], self.family_combo)
        form.addRow(self._widgets["recv.add_group_addr"], self.group_edit)
        form.addRow(self._widgets["recv.add_port"], self.port_edit)
        form.addRow(self._widgets["recv.add_iface"], self.iface_combo)
        form.addRow(self._widgets["recv.add_version"], self.version_combo)
        form.addRow(self._widgets["recv.add_sources"], self.sources_edit)
        form.addRow(self._widgets["recv.add_btn"], self.add_btn)
        root.addWidget(add_box)

        # Remote sender monitor (NEW) --------------------------------------
        self._build_remote_panel(root)

        # Active memberships table -----------------------------------------
        self._build_table_panel(root)

        # Log ----------------------------------------------------------------
        self._build_log_panel(root)

        # Initial population
        self._populate_interfaces(AddressFamily.IPV4)
        self.retranslate_ui()
        # After language strings are loaded, do a final header refresh
        self._set_table_headers()
        QTimer.singleShot(0, self._refill_group_column)

    def _label_for(self, form: QFormLayout, buddy: QWidget) -> QLabel:
        lbl = QLabel()
        lbl.setBuddy(buddy)
        return lbl

    def _build_remote_panel(self, root: QVBoxLayout) -> None:
        box = QGroupBox()
        self._widgets["recv.remote_monitor"] = box
        outer = QVBoxLayout(box)

        # Address + interval row
        top = QHBoxLayout()
        self.remote_addr_label = QLabel()
        self.remote_addr_edit = QLineEdit()
        self.remote_addr_edit.setPlaceholderText(i18n.t("recv.remote_address_ph"))
        self.remote_interval_label = QLabel()
        self.remote_interval = QDoubleSpinBox()
        self.remote_interval.setRange(0.2, 60.0)
        self.remote_interval.setSingleStep(0.2)
        self.remote_interval.setValue(1.0)
        self.remote_interval.setDecimals(1)
        self.remote_interval_unit = QLabel(i18n.t("recv.remote_unit_sec"))
        self.remote_connect_btn = QPushButton()
        self.remote_disconnect_btn = QPushButton()
        self.remote_disconnect_btn.setEnabled(False)

        top.addWidget(self.remote_addr_label)
        top.addWidget(self.remote_addr_edit, 1)
        top.addSpacing(8)
        top.addWidget(self.remote_interval_label)
        top.addWidget(self.remote_interval)
        top.addWidget(self.remote_interval_unit)
        top.addSpacing(8)
        top.addWidget(self.remote_connect_btn)
        top.addWidget(self.remote_disconnect_btn)
        outer.addLayout(top)

        # Status row
        status_row = QHBoxLayout()
        self.remote_status_label = QLabel()
        self.remote_status_value = QLabel(i18n.t("recv.remote_status_idle"))
        status_row.addWidget(self.remote_status_label)
        status_row.addWidget(self.remote_status_value, 1)
        outer.addLayout(status_row)

        # Stats grid (two columns of label/value pairs)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        # Left column: target / mode / elapsed
        # Right column: sent / bytes / rates
        self.remote_target_label = QLabel()
        self.remote_target_value = QLabel("--")
        self.remote_mode_label = QLabel()
        self.remote_mode_value = QLabel("--")
        self.remote_elapsed_label = QLabel()
        self.remote_elapsed_value = QLabel("--")
        self.remote_sent_label = QLabel()
        self.remote_sent_value = QLabel("--")
        self.remote_bytes_label = QLabel()
        self.remote_bytes_value = QLabel("--")
        self.remote_pps_label = QLabel()
        self.remote_pps_value = QLabel("--")
        self.remote_bps_label = QLabel()
        self.remote_bps_value = QLabel("--")
        self.remote_pps_value.setStyleSheet("font-weight: bold;")
        self.remote_bps_value.setStyleSheet("font-weight: bold;")

        grid.addWidget(self.remote_target_label, 0, 0)
        grid.addWidget(self.remote_target_value, 0, 1)
        grid.addWidget(self.remote_mode_label, 0, 2)
        grid.addWidget(self.remote_mode_value, 0, 3)
        grid.addWidget(self.remote_elapsed_label, 0, 4)
        grid.addWidget(self.remote_elapsed_value, 0, 5)
        grid.addWidget(self.remote_sent_label, 1, 0)
        grid.addWidget(self.remote_sent_value, 1, 1)
        grid.addWidget(self.remote_bytes_label, 1, 2)
        grid.addWidget(self.remote_bytes_value, 1, 3)
        grid.addWidget(self.remote_pps_label, 1, 4)
        grid.addWidget(self.remote_pps_value, 1, 5)
        grid.addWidget(self.remote_bps_label, 1, 5 + 0)  # span handled below
        # Simpler: put bps on row 2
        grid.removeWidget(self.remote_bps_label)
        grid.removeWidget(self.remote_bps_value)
        grid.addWidget(self.remote_pps_label, 1, 4)
        grid.addWidget(self.remote_pps_value, 1, 5)
        grid.addWidget(self.remote_bps_label, 2, 4)
        grid.addWidget(self.remote_bps_value, 2, 5)
        grid.addWidget(self.remote_bps_label, 1, 4)
        # Actually simpler: just place them sequentially
        labels_values = [
            (self.remote_target_label, self.remote_target_value),
            (self.remote_mode_label,   self.remote_mode_value),
            (self.remote_elapsed_label, self.remote_elapsed_value),
            (self.remote_sent_label,    self.remote_sent_value),
            (self.remote_bytes_label,   self.remote_bytes_value),
            (self.remote_pps_label,     self.remote_pps_value),
            (self.remote_bps_label,     self.remote_bps_value),
        ]
        # Reset the grid and place cleanly
        for i in reversed(range(grid.count())):
            item = grid.takeAt(i)
        for i, (lbl, val) in enumerate(labels_values):
            r, c = divmod(i, 4)
            grid.addWidget(lbl, r, c * 2)
            grid.addWidget(val, r, c * 2 + 1)
        outer.addLayout(grid)

        # Cache labels for retranslation
        self._widgets["recv.remote_address"] = self.remote_addr_label
        self._widgets["recv.remote_interval"] = self.remote_interval_label
        self._widgets["recv.remote_connect"] = self.remote_connect_btn
        self._widgets["recv.remote_disconnect"] = self.remote_disconnect_btn
        self._widgets["recv.remote_status"] = self.remote_status_label
        self._widgets["recv.remote_target"] = self.remote_target_label
        self._widgets["recv.remote_mode"] = self.remote_mode_label
        self._widgets["recv.remote_elapsed"] = self.remote_elapsed_label
        self._widgets["recv.remote_sent"] = self.remote_sent_label
        self._widgets["recv.remote_bytes"] = self.remote_bytes_label
        self._widgets["recv.remote_pps"] = self.remote_pps_label
        self._widgets["recv.remote_bps"] = self.remote_bps_label

        root.addWidget(box)

    def _build_table_panel(self, root: QVBoxLayout) -> None:
        box = QGroupBox()
        self._widgets["recv.active"] = box
        tlay = QVBoxLayout(box)
        self.table = QTableWidget(0, len(self.HEADER_KEYS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        hdr = self.table.horizontalHeader()
        for i in range(len(self.HEADER_KEYS)):
            hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        for col, w in enumerate([40, 200, 60, 120, 80, 120, 90, 100, 90, 110, 80]):
            self.table.setColumnWidth(col, w)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._group_user_resized = False
        self._suppress_resize_signal = False
        hdr.sectionResized.connect(self._on_section_resized)
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        self._settings = QSettings("multicast-tool", "Multicast Test Tool")
        self._restore_header_state()
        tlay.addWidget(self.table)

        bar = QHBoxLayout()
        self.remove_btn = QPushButton()
        self.clear_btn = QPushButton()
        self.reset_cols_btn = QPushButton()
        self.stop_all_btn = QPushButton()
        self._widgets["recv.btn_remove"] = self.remove_btn
        self._widgets["recv.btn_reset_counters"] = self.clear_btn
        self._widgets["recv.btn_reset_columns"] = self.reset_cols_btn
        self._widgets["recv.btn_stop_all"] = self.stop_all_btn
        bar.addWidget(self.remove_btn)
        bar.addWidget(self.clear_btn)
        bar.addWidget(self.reset_cols_btn)
        bar.addStretch(1)
        bar.addWidget(self.stop_all_btn)
        tlay.addLayout(bar)
        root.addWidget(box, 1)

    def _build_log_panel(self, root: QVBoxLayout) -> None:
        box = QGroupBox()
        self._widgets["recv.log"] = box
        llay = QVBoxLayout(box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setFont(QFont("Consolas", 9))
        llay.addWidget(self.log)
        root.addWidget(box, 1)

    def _wire(self) -> None:
        self.add_btn.clicked.connect(self._on_add_clicked)
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        self.stop_all_btn.clicked.connect(self._on_stop_all_clicked)
        self.clear_btn.clicked.connect(self._on_clear_counters_clicked)
        self.reset_cols_btn.clicked.connect(self._on_reset_columns_clicked)
        self.family_combo.currentTextChanged.connect(self._on_family_changed)
        self.bus.receiver_event.connect(self._on_receiver_event)
        self.remote_connect_btn.clicked.connect(self._on_remote_connect)
        self.remote_disconnect_btn.clicked.connect(self._on_remote_disconnect)

    # -- translation ------------------------------------------------------ #

    def retranslate_ui(self) -> None:
        for key, w in self._widgets.items():
            text = i18n.t(key)
            if isinstance(w, QGroupBox):
                w.setTitle(text)
            elif isinstance(w, QPushButton):
                w.setText(text)
            else:
                w.setText(text)
        # Family / version / interface defaults are also language-aware
        family = self._current_family()
        ver_items = (
            [i18n.t(f"ver.{v.value.lower()}") for v in IgmpVersion]
            if family is AddressFamily.IPV4
            else [i18n.t(f"ver.{v.value.lower()}") for v in MldVersion]
        )
        ver_cur = self.version_combo.currentText() or i18n.t(f"ver.{('igmpv2' if family is AddressFamily.IPV4 else 'mldv2')}")
        if ver_cur not in ver_items:
            ver_cur = ver_items[1] if len(ver_items) > 1 else ver_items[0]
        _fill_combo(self.version_combo, ver_items, ver_cur)

        # Family combo uses raw values (used as protocol identifiers) but
        # we still want the displayed text to follow the language.
        fam_items = [i18n.t(f"family.{f.value.lower()}") for f in AddressFamily]
        # If the current selection was "IPv4" (English) and we switch to
        # Chinese, it becomes "IPv4" still; map by enum.
        cur = self.family_combo.currentText()
        fam_cur = i18n.t(f"family.{family.value.lower()}")
        _fill_combo(self.family_combo, fam_items, fam_cur)
        # Re-populate interface combo to translate the "(default)" entry
        self._populate_interfaces(family)
        # Placeholders
        self.group_edit.setPlaceholderText(i18n.t("recv.placeholder_group"))
        self.sources_edit.setPlaceholderText(i18n.t("recv.placeholder_sources"))
        self.remote_addr_edit.setPlaceholderText(i18n.t("recv.remote_address_ph"))
        self.remote_interval_unit.setText(i18n.t("recv.remote_unit_sec"))
        # Table headers
        self._set_table_headers()

    def _set_table_headers(self) -> None:
        self.table.setHorizontalHeaderLabels([i18n.t(k) for k in self.HEADER_KEYS])

    def _current_family(self) -> AddressFamily:
        # Resolve current family by matching either the raw enum value
        # (English mode) or the translated label (Chinese mode).
        cur = self.family_combo.currentText()
        for f in AddressFamily:
            if cur in (f.value, i18n.t(f"family.{f.value.lower()}")):
                return f
        return AddressFamily.IPV4

    def _current_version(self):
        cur = self.version_combo.currentText()
        family = self._current_family()
        versions = IgmpVersion if family is AddressFamily.IPV4 else MldVersion
        for v in versions:
            if cur in (v.value, i18n.t(f"ver.{v.value.lower()}")):
                return v
        return versions[1] if len(versions) > 1 else versions[0]

    # -- add / remove ----------------------------------------------------- #

    def _on_family_changed(self, _text: str) -> None:
        family = self._current_family()
        if family is AddressFamily.IPV4:
            self.group_edit.setText("224.0.0.1")
        else:
            self.group_edit.setText("ff02::1")
        self._populate_interfaces(family)
        # Refresh version combo options
        self.retranslate_ui()

    def _populate_interfaces(self, family: AddressFamily) -> None:
        current = self.iface_combo.currentText()
        self.iface_combo.blockSignals(True)
        self.iface_combo.clear()
        self.iface_combo.addItem(i18n.t("iface.default"))
        for ip in local_addresses(family):
            self.iface_combo.addItem(ip)
        if current and current != i18n.t("iface.default"):
            self.iface_combo.setCurrentText(current)
        self.iface_combo.blockSignals(False)

    def _on_add_clicked(self) -> None:
        try:
            family = self._current_family()
            group = self.group_edit.text().strip()
            port = int(self.port_edit.value())
            iface = self.iface_combo.currentText().strip()
            if iface == i18n.t("iface.default"):
                iface = ""
            version_obj = self._current_version()
            sources = parse_source_list(self.sources_edit.text(), family)
        except ValueError as e:
            QMessageBox.warning(self, i18n.t("dlg.invalid_input"), str(e))
            return

        if not is_multicast_group(group, family):
            QMessageBox.warning(
                self, i18n.t("dlg.invalid_group"),
                f"{group!r} \u4e0d\u662f\u6709\u6548\u7684 {family.value} \u7ec4\u64ad\u5730\u5740\u3002"
                if i18n.get_language() == "zh_CN" else
                f"{group!r} is not a valid {family.value} multicast address."
            )
            return

        if family is AddressFamily.IPV4:
            cfg = ReceiverConfig(
                family=family, group=group, port=port, interface=iface,
                igmp_version=version_obj, sources=sources,
            )
        else:
            cfg = ReceiverConfig(
                family=family, group=group, port=port, interface=iface,
                mld_version=version_obj, sources=sources,
            )

        row_id = self.state.next_row_id
        self.state.next_row_id += 1
        stats = StatsTracker()
        rcv = MulticastReceiver(cfg, stats)
        self._append_log(
            f"{i18n.t('recv.add_group')}: {family.value} {group}:{port} "
            f"v{version_obj.value} iface={iface or i18n.t('iface.default')}"
        )
        try:
            rcv.start()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, i18n.t("dlg.failed_start"), f"{e}\n{traceback.format_exc()}")
            return
        row = ReceiverRow(
            row_id=row_id, receiver=rcv, stats=stats, config=cfg,
            started_at=time.monotonic(),
        )
        self.state.receivers[row_id] = row
        rcv.on_event = lambda ev, rid=row_id: self.bus.receiver_event.emit(rid, ev)
        self._insert_table_row(row)

    def _on_remove_clicked(self) -> None:
        sel = self.table.currentRow()
        if sel < 0:
            return
        row_id = int(self.table.item(sel, 0).text())
        self._stop_row(row_id)

    def _on_stop_all_clicked(self) -> None:
        for row_id in list(self.state.receivers.keys()):
            self._stop_row(row_id)

    def _on_clear_counters_clicked(self) -> None:
        sel = self.table.currentRow()
        if sel < 0:
            return
        row_id = int(self.table.item(sel, 0).text())
        row = self.state.receivers.get(row_id)
        if row is not None:
            row.stats.reset()

    def _on_reset_columns_clicked(self) -> None:
        self._settings.remove("receive_table_header")
        self._settings.remove("receive_group_user_resized")
        hdr = self.table.horizontalHeader()
        for i in range(len(self.HEADER_KEYS)):
            hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        for col, w in enumerate([40, 200, 60, 120, 80, 120, 90, 100, 90, 110, 80]):
            self.table.setColumnWidth(col, w)
        self._group_user_resized = False
        self._refill_group_column()

    def _restore_header_state(self) -> None:
        saved = self._settings.value("receive_table_header")
        if saved is not None:
            try:
                if isinstance(saved, str):
                    saved = QByteArray.fromBase64(saved.encode("ascii"))
                elif isinstance(saved, (bytes, bytearray)):
                    saved = QByteArray(bytes(saved))
                self.table.horizontalHeader().restoreState(saved)
            except Exception:  # noqa: BLE001
                logger.debug("Failed to restore table header state", exc_info=True)
        hdr = self.table.horizontalHeader()
        for i in range(len(self.HEADER_KEYS)):
            hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        self._group_user_resized = bool(
            self._settings.value("receive_group_user_resized", False)
        )
        if hdr.sectionSize(1) < 40:
            self._group_user_resized = False

    def save_settings(self) -> None:
        state = self.table.horizontalHeader().saveState()
        self._settings.setValue(
            "receive_table_header", bytes(state.toBase64()).decode("ascii")
        )
        self._settings.setValue("receive_group_user_resized", self._group_user_resized)

    # -- table mechanics -------------------------------------------------- #

    def eventFilter(self, watched, event):  # noqa: N802
        # The table itself emits Resize when its outer size changes, but
        # the viewport can also grow when a vertical scrollbar disappears
        # without the table widget itself resizing. Listen to both.
        if event.type() == QEvent.Resize and (
            watched is self.table or watched is self.table.viewport()
        ):
            self._refill_group_column()
        return super().eventFilter(watched, event)

    def _on_section_resized(self, index: int, old: int, new: int) -> None:
        if self._suppress_resize_signal:
            return
        if index == 1 and old != new:
            self._group_user_resized = True

    def _refill_group_column(self) -> None:
        if self._group_user_resized:
            return
        hdr = self.table.horizontalHeader()
        if hdr is None:
            return
        viewport = self.table.viewport()
        if viewport is None:
            return
        vp_width = viewport.width()
        if vp_width <= 0:
            return
        others = sum(hdr.sectionSize(i) for i in range(len(self.HEADER_KEYS)) if i != 1)
        vh = self.table.verticalHeader()
        vh_w = vh.width() if vh is not None and vh.isVisible() else 0
        target = max(40, vp_width - others + vh_w)
        if hdr.sectionSize(1) == target:
            return
        self._suppress_resize_signal = True
        try:
            hdr.resizeSection(1, target)
        finally:
            self._suppress_resize_signal = False

    def _insert_table_row(self, row: ReceiverRow) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        cells = [str(row.row_id), row.config.group, str(row.config.port),
                 row.config.interface or i18n.t("iface.default"),
                 (row.config.igmp_version if row.config.family is AddressFamily.IPV4
                  else row.config.mld_version).value]
        cells.append(", ".join(row.config.sources) if row.config.sources else "\u2014")
        for c in range(len(self.HEADER_KEYS) - 5):
            self.table.setItem(r, c, QTableWidgetItem(cells[c]))
        for c in range(len(self.HEADER_KEYS) - 5, len(self.HEADER_KEYS)):
            item = QTableWidgetItem("0")
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, c, item)

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(line.rstrip())

    def _stop_row(self, row_id: int) -> None:
        row = self.state.receivers.pop(row_id, None)
        if row is None:
            return
        try:
            row.receiver.stop()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to stop receiver")
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0) and int(self.table.item(r, 0).text()) == row_id:
                self.table.removeRow(r)
                break
        self._append_log(f"row #{row_id} \u79fb\u9664" if i18n.get_language() == "zh_CN"
                         else f"row #{row_id} removed")

    def _on_receiver_event(self, row_id: int, event: ReceiverEvent) -> None:
        row = self.state.receivers.get(row_id)
        if row is None:
            return
        ts = time.strftime("%H:%M:%S")
        if event.type is ReceiverEventType.STARTED:
            self._append_log(f"[{ts}] {row.config.group} #{row_id} {event.message}")
        elif event.type is ReceiverEventType.STOPPED:
            self._append_log(f"[{ts}] {row.config.group} #{row_id} {event.message}")
        elif event.type is ReceiverEventType.ERROR:
            self._append_log(f"[{ts}] {row.config.group} #{row_id} ERROR: {event.message}")

    # -- remote sender ---------------------------------------------------- #

    def _on_remote_connect(self) -> None:
        addr = self.remote_addr_edit.text().strip()
        if not addr:
            QMessageBox.warning(self, i18n.t("dlg.invalid_input"),
                                "\u8bf7\u8f93\u5165\u53d1\u9001\u7aef\u5730\u5740\uff08host:port\uff09"
                                if i18n.get_language() == "zh_CN" else
                                "Please enter sender address (host:port)")
            return
        interval = float(self.remote_interval.value())
        if self.state.remote_poller is not None:
            try:
                self.state.remote_poller.stop()
            except Exception:  # noqa: BLE001
                pass
        # validate_host_port raises ValueError for malformed addresses
        # (no scheme, no path, rejects multicast / link-local / etc.)
        try:
            poller = RemoteSenderPoller(addr, interval_sec=interval)
        except ValueError as e:
            QMessageBox.warning(self, i18n.t("dlg.invalid_input"), str(e))
            return
        try:
            poller.start()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, i18n.t("dlg.failed_start"), str(e))
            return
        self.state.remote_poller = poller
        self.remote_connect_btn.setEnabled(False)
        self.remote_disconnect_btn.setEnabled(True)
        self.remote_status_value.setText(i18n.t("recv.remote_status_connecting"))
        self._append_log(f"remote sender: {addr} ({interval}s)")

    def _on_remote_disconnect(self) -> None:
        poller, self.state.remote_poller = self.state.remote_poller, None
        if poller is not None:
            try:
                poller.stop()
            except Exception:  # noqa: BLE001
                pass
        self.remote_connect_btn.setEnabled(True)
        self.remote_disconnect_btn.setEnabled(False)
        self.remote_status_value.setText(i18n.t("recv.remote_status_idle"))
        for lbl in (self.remote_target_value, self.remote_mode_value, self.remote_elapsed_value,
                    self.remote_sent_value, self.remote_bytes_value,
                    self.remote_pps_value, self.remote_bps_value):
            lbl.setText("--")
        self._append_log("remote sender: \u65ad\u5f00" if i18n.get_language() == "zh_CN"
                         else "remote sender: disconnected")

    def refresh_remote(self) -> None:
        poller = self.state.remote_poller
        if poller is None or not poller.is_running:
            return
        snap: RemoteSenderSnapshot = poller.snapshot()
        if snap.status == "error":
            self.remote_status_value.setText(
                f"{i18n.t('recv.remote_status_err')}: {snap.error}"
            )
            return
        if snap.status == "connecting" and not snap.is_fresh:
            self.remote_status_value.setText(i18n.t("recv.remote_status_connecting"))
            return
        self.remote_status_value.setText(i18n.t("recv.remote_status_ok"))
        # Target / mode
        if snap.target_group:
            fam = f" ({snap.target_family})" if snap.target_family else ""
            self.remote_target_value.setText(
                f"{snap.target_group}:{snap.target_port}{fam}"
            )
        else:
            self.remote_target_value.setText("--")
        self.remote_mode_value.setText(snap.mode or "--")
        self.remote_elapsed_value.setText(_format_elapsed(snap.elapsed_sec))
        self.remote_sent_value.setText(f"{snap.sent_packets:,}")
        self.remote_bytes_value.setText(format_bytes(snap.sent_bytes))
        self.remote_pps_value.setText(f"{snap.pps:,.1f} pps")
        self.remote_bps_value.setText(format_rate_bps(snap.bps))
        # Colour-code the rates the same way as the table
        self.remote_pps_value.setForeground(_color_for_pps(snap.pps))
        self.remote_bps_value.setForeground(_color_for_pps(snap.pps))

    # -- periodic refresh -------------------------------------------------- #

    def refresh_stats(self) -> None:
        self.refresh_remote()
        for r in range(self.table.rowCount()):
            row_id_item = self.table.item(r, 0)
            if row_id_item is None:
                continue
            try:
                row_id = int(row_id_item.text())
            except ValueError:
                continue
            row = self.state.receivers.get(row_id)
            if row is None:
                continue
            snap: StatsSnapshot = row.stats.snapshot()
            self.table.item(r, 6).setText(f"{snap.total_packets:,}")
            self.table.item(r, 7).setText(format_bytes(snap.total_bytes))
            pps_item = self.table.item(r, 8)
            pps_item.setText(f"{snap.pps:,.1f}")
            pps_item.setForeground(_color_for_pps(snap.pps))
            bps_item = self.table.item(r, 9)
            bps_item.setText(format_rate_bps(snap.bps))
            bps_item.setForeground(_color_for_pps(snap.pps))
            self.table.item(r, 10).setText(_format_elapsed(snap.elapsed_sec))


# --------------------------------------------------------------------------- #
# Send tab                                                                    #
# --------------------------------------------------------------------------- #


class SendTab(QWidget):
    """Send IPv4 / IPv6 multicast traffic, optionally exposing live stats
    over HTTP for a remote receiver to mirror."""

    def __init__(self, state: AppState, bus: _SignalBus, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.bus = bus
        self._widgets: dict[str, QWidget] = {}
        self._build_ui()
        self._wire()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Send parameters ----------------------------------------------------
        self._build_params_box(root)

        # Stats exporter (NEW) ---------------------------------------------
        self._build_exporter_box(root)

        # Send log ----------------------------------------------------------
        self._build_log_box(root)

        self._populate_ifaces(AddressFamily.IPV4)
        self.retranslate_ui()
        QTimer.singleShot(0, self._refresh_exporter_endpoint_hint)

    def _build_params_box(self, root: QVBoxLayout) -> None:
        box = QGroupBox()
        self._widgets["send.params"] = box
        form = QFormLayout(box)

        self.s_family = QComboBox()
        self.s_group = QLineEdit("239.1.1.1")
        self.s_port = QSpinBox(); self.s_port.setRange(1, 65535); self.s_port.setValue(5000)
        self.s_iface = QComboBox()
        self.s_iface.setEditable(True)
        self.s_source = QLineEdit()
        self.s_ttl = QSpinBox(); self.s_ttl.setRange(0, 255); self.s_ttl.setValue(1)
        self.s_payload = QSpinBox(); self.s_payload.setRange(0, 65507); self.s_payload.setValue(1024); self.s_payload.setSingleStep(64)
        self.s_mode = QComboBox()
        self.s_count = QSpinBox(); self.s_count.setRange(1, 10_000_000); self.s_count.setValue(1000)
        self.s_rate = QSpinBox(); self.s_rate.setRange(1, 1_000_000); self.s_rate.setValue(1000)
        self.s_template = QCheckBox()
        self.s_template_text = QLineEdit("hello multicast")
        self.s_template_text.setEnabled(False)
        self.s_template.toggled.connect(self.s_template_text.setEnabled)
        self.s_start = QPushButton()
        self.s_stop = QPushButton()
        self.s_stop.setEnabled(False)
        self.s_progress = QProgressBar(); self.s_progress.setRange(0, 100); self.s_progress.setValue(0)
        self.s_status = QLabel()

        # Build the rate row as a composite (spinbox + "pps" unit) so the
        # unit label can be re-translated on language switch.
        self._rate_unit_label = QLabel(i18n.t("send.unit_pps"))
        self._rate_widget = QWidget()
        rate_lay = QHBoxLayout(self._rate_widget)
        rate_lay.setContentsMargins(0, 0, 0, 0)
        rate_lay.addWidget(self.s_rate)
        rate_lay.addWidget(self._rate_unit_label)
        rate_lay.addStretch(1)

        # Each entry is (key, field-widget, label-buddy). The buddy is the
        # widget the label points at via setBuddy (the spinbox / combobox
        # for focus shortcut, the composite QWidget otherwise).
        rows: list[tuple[str, QWidget, QWidget]] = [
            ("send.family", self.s_family, self.s_family),
            ("send.group", self.s_group, self.s_group),
            ("send.port", self.s_port, self.s_port),
            ("send.iface", self.s_iface, self.s_iface),
            ("send.source", self.s_source, self.s_source),
            ("send.ttl", self.s_ttl, self.s_ttl),
            ("send.payload", self.s_payload, self.s_payload),
            ("send.mode", self.s_mode, self.s_mode),
            ("send.count", self.s_count, self.s_count),
            ("send.rate", self._rate_widget, self.s_rate),
        ]
        for key, field_widget, buddy in rows:
            lbl = QLabel()
            lbl.setBuddy(buddy)
            form.addRow(lbl, field_widget)
            self._widgets[key] = lbl

        # Template row: checkbox on the left, text input on the right.
        form.addRow(self.s_template, self.s_template_text)
        self._widgets["send.template"] = self.s_template

        # Control buttons
        ctl = QHBoxLayout()
        ctl.addWidget(self.s_start)
        ctl.addWidget(self.s_stop)
        ctl.addStretch(1)
        ctl_w = QWidget(); ctl_w.setLayout(ctl)
        form.addRow(ctl_w)
        self._widgets["send.btn_start"] = self.s_start
        self._widgets["send.btn_stop"] = self.s_stop

        # Progress and status
        self._widgets["send.progress"] = QLabel()
        form.addRow(self._widgets["send.progress"], self.s_progress)
        self._widgets["send.status"] = QLabel()
        form.addRow(self._widgets["send.status"], self.s_status)
        root.addWidget(box)

    def _build_exporter_box(self, root: QVBoxLayout) -> None:
        box = QGroupBox()
        self._widgets["send.expose"] = box
        outer = QVBoxLayout(box)
        top = QHBoxLayout()
        self.exp_port_label = QLabel()
        self.exp_port = QSpinBox()
        self.exp_port.setRange(1, 65535)
        self.exp_port.setValue(8765)
        self.exp_start = QPushButton()
        self.exp_stop = QPushButton()
        self.exp_stop.setEnabled(False)
        top.addWidget(self.exp_port_label)
        top.addWidget(self.exp_port)
        top.addStretch(1)
        top.addWidget(self.exp_start)
        top.addWidget(self.exp_stop)
        outer.addLayout(top)
        # Network-bind checkbox (security): default = loopback only.
        self.exp_bind_net = QCheckBox()
        self.exp_bind_net.toggled.connect(self._refresh_exporter_endpoint_hint)
        outer.addWidget(self.exp_bind_net)
        self._widgets["send.expose_bind_net"] = self.exp_bind_net
        # Status
        status_row = QHBoxLayout()
        self.exp_status_label = QLabel()
        self.exp_status_value = QLabel(i18n.t("send.expose_status_off"))
        status_row.addWidget(self.exp_status_label)
        status_row.addWidget(self.exp_status_value, 1)
        outer.addLayout(status_row)
        # Endpoint hint
        hint_row = QHBoxLayout()
        self.exp_endpoint_label = QLabel()
        self.exp_endpoint_value = QLabel("--")
        self.exp_endpoint_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.exp_endpoint_value.setStyleSheet("color: #666; font-family: Consolas, monospace;")
        hint_row.addWidget(self.exp_endpoint_label)
        hint_row.addWidget(self.exp_endpoint_value, 1)
        outer.addLayout(hint_row)
        self._widgets["send.expose_port"] = self.exp_port_label
        self._widgets["send.expose_start"] = self.exp_start
        self._widgets["send.expose_stop"] = self.exp_stop
        self._widgets["send.expose_status_off_marker"] = self.exp_status_label
        self._widgets["send.expose_endpoint"] = self.exp_endpoint_label
        root.addWidget(box)

    def _build_log_box(self, root: QVBoxLayout) -> None:
        box = QGroupBox()
        self._widgets["send.log"] = box
        llay = QVBoxLayout(box)
        self.s_log = QPlainTextEdit()
        self.s_log.setReadOnly(True)
        self.s_log.setMaximumBlockCount(2000)
        self.s_log.setFont(QFont("Consolas", 9))
        llay.addWidget(self.s_log)
        root.addWidget(box, 1)

    def _wire(self) -> None:
        self.s_start.clicked.connect(self._on_start_clicked)
        self.s_stop.clicked.connect(self._on_stop_clicked)
        self.s_family.currentTextChanged.connect(self._on_family_changed)
        self.s_mode.currentTextChanged.connect(self._on_mode_changed)
        self.bus.sender_event.connect(self._on_sender_event)
        self.exp_start.clicked.connect(self._on_exporter_start)
        self.exp_stop.clicked.connect(self._on_exporter_stop)
        self.exp_port.valueChanged.connect(lambda _: self._refresh_exporter_endpoint_hint())

    # -- translation ------------------------------------------------------ #

    def retranslate_ui(self) -> None:
        for key, w in self._widgets.items():
            text = i18n.t(key)
            if isinstance(w, QGroupBox):
                w.setTitle(text)
            elif isinstance(w, QLabel) and key.startswith("send."):
                w.setText(text)
            else:
                w.setText(text)
        # Family combo (use translated labels but keep enum value identifiable)
        family = self._current_family()
        fam_items = [i18n.t(f"family.{f.value.lower()}") for f in AddressFamily]
        _fill_combo(self.s_family, fam_items, i18n.t(f"family.{family.value.lower()}"))
        # Mode combo
        mode_items = [i18n.t(f"mode.{m.value}") for m in SenderMode]
        cur = self.s_mode.currentText()
        cur_match = next(
            (m for m in SenderMode
             if cur in (m.value, i18n.t(f"mode.{m.value}"))),
            SenderMode.BURST,
        )
        _fill_combo(self.s_mode, mode_items, i18n.t(f"mode.{cur_match.value}"))
        # Rate unit suffix
        self._rate_unit_label.setText(i18n.t("send.unit_pps"))
        # Defaults
        self.s_source.setPlaceholderText(i18n.t("send.placeholder_source"))
        # Exporter status
        if self.state.stats_exporter is not None and self.state.stats_exporter.is_running:
            self._update_exporter_status(running=True)
        else:
            self._update_exporter_status(running=False)
        # Re-populate interfaces to translate "(default)"
        self._populate_ifaces(family)
        # Mode enable/disable
        self._on_mode_changed(self.s_mode.currentText())

    def _current_family(self) -> AddressFamily:
        cur = self.s_family.currentText()
        for f in AddressFamily:
            if cur in (f.value, i18n.t(f"family.{f.value.lower()}")):
                return f
        return AddressFamily.IPV4

    def _on_family_changed(self, _text: str) -> None:
        family = self._current_family()
        if family is AddressFamily.IPV4:
            self.s_group.setText("239.1.1.1")
        else:
            self.s_group.setText("ff3e::1")
        self._populate_ifaces(family)

    def _populate_ifaces(self, family: AddressFamily) -> None:
        current = self.s_iface.currentText()
        self.s_iface.blockSignals(True)
        self.s_iface.clear()
        self.s_iface.addItem(i18n.t("iface.default"))
        for ip in local_addresses(family):
            self.s_iface.addItem(ip)
        if current and current != i18n.t("iface.default"):
            self.s_iface.setCurrentText(current)
        self.s_iface.blockSignals(False)

    def _on_mode_changed(self, _text: str) -> None:
        cur = self.s_mode.currentText()
        match = next(
            (m for m in SenderMode
             if cur in (m.value, i18n.t(f"mode.{m.value}"))),
            SenderMode.BURST,
        )
        # Count only applies to finite sends (Burst / Rate-limited);
        # Rate applies to anything that paces packets (Rate-limited / Continuous).
        self.s_count.setEnabled(match in (SenderMode.BURST, SenderMode.RATE_LIMITED))
        self.s_rate.setEnabled(match in (SenderMode.RATE_LIMITED, SenderMode.CONTINUOUS))

    # -- send control ----------------------------------------------------- #

    def _on_start_clicked(self) -> None:
        if self.state.sender is not None and self.state.sender.is_running():
            return
        try:
            family = self._current_family()
            group = self.s_group.text().strip()
            port = int(self.s_port.value())
            iface = self.s_iface.currentText().strip()
            if iface == i18n.t("iface.default"):
                iface = ""
            source = self.s_source.text().strip()
            ttl = int(self.s_ttl.value())
            payload_size = int(self.s_payload.value())
            mode_cur = self.s_mode.currentText()
            mode = next(
                (m for m in SenderMode
                 if mode_cur in (m.value, i18n.t(f"mode.{m.value}"))),
                SenderMode.BURST,
            )
            count = int(self.s_count.value())
            rate = int(self.s_rate.value())
            template = self.s_template_text.text().encode("utf-8") if self.s_template.isChecked() else b""
        except ValueError as e:
            QMessageBox.warning(self, i18n.t("dlg.invalid_input"), str(e))
            return
        if not is_multicast_group(group, family):
            QMessageBox.warning(self, i18n.t("dlg.invalid_group"),
                                f"{group!r} \u4e0d\u662f\u6709\u6548\u7684 {family.value} \u7ec4\u64ad\u5730\u5740"
                                if i18n.get_language() == "zh_CN" else
                                f"{group!r} is not a valid {family.value} multicast address.")
            return

        cfg = SenderConfig(
            family=family, group=group, port=port, interface=iface, source=source,
            ttl=ttl, payload_size=payload_size, mode=mode, count=count,
            rate_pps=rate, payload_template=template,
        )
        sender = MulticastSender(cfg)
        try:
            sender.start()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, i18n.t("dlg.failed_start"), f"{e}\n{traceback.format_exc()}")
            return
        sender.on_event = lambda ev: self.bus.sender_event.emit(ev)
        self.state.sender = sender
        self.s_status.setText("Running")
        self.s_start.setEnabled(False)
        self.s_stop.setEnabled(True)
        self.s_progress.setValue(0)
        self._append_log(
            f"start: {family.value} {group}:{port} mode={mode.value} ttl={ttl} payload={payload_size}"
        )
        # If a stats exporter is running, rebuild its provider
        self._refresh_exporter_provider()

    def _on_stop_clicked(self) -> None:
        if self.state.sender is None:
            return
        sent = self.state.sender._sent
        self.state.sender.stop()
        self._append_log(f"stop: {sent} \u5305\u5df2\u53d1\u9001" if i18n.get_language() == "zh_CN"
                         else f"stop: {sent} packets sent")
        self.state.sender = None
        self.s_status.setText(i18n.t("send.status_idle"))
        self.s_start.setEnabled(True)
        self.s_stop.setEnabled(False)
        self._refresh_exporter_provider()

    def _on_sender_event(self, event: SenderEvent) -> None:
        ts = time.strftime("%H:%M:%S")
        if event.type is SenderEventType.STARTED:
            self._append_log(f"[{ts}] {event.message}")
        elif event.type is SenderEventType.STOPPED:
            self._append_log(f"[{ts}] {event.message}")
            self.s_status.setText(i18n.t("send.status_idle"))
            self.s_start.setEnabled(True)
            self.s_stop.setEnabled(False)
            if self.state.sender is not None and event.sent:
                self.s_progress.setValue(100)
        elif event.type is SenderEventType.PROGRESS:
            if event.target > 0:
                pct = int(event.sent * 100 / event.target)
                self.s_progress.setValue(min(100, pct))
                self.s_status.setText(
                    f"{event.sent:,} / {event.target:,} ({pct}%)"
                )
            else:
                self.s_status.setText(f"{event.sent:,}")
        elif event.type is SenderEventType.ERROR:
            self._append_log(f"[{ts}] ERROR: {event.message}")

    def _append_log(self, line: str) -> None:
        self.s_log.appendPlainText(line.rstrip())

    # -- stats exporter (sender side) ------------------------------------ #

    def _on_exporter_start(self) -> None:
        if self.state.stats_exporter is not None and self.state.stats_exporter.is_running:
            return
        port = int(self.exp_port.value())
        bind = "0.0.0.0" if self.exp_bind_net.isChecked() else StatsExporter.DEFAULT_BIND
        exporter = StatsExporter(port, stats_provider=self._make_stats_provider(), bind=bind)
        try:
            exporter.start()
        except OSError as e:
            QMessageBox.critical(self, i18n.t("dlg.failed_start"),
                                 f"{e}\n{traceback.format_exc()}")
            return
        self.state.stats_exporter = exporter
        self._update_exporter_status(running=True)
        self._refresh_exporter_endpoint_hint()
        self.exp_start.setEnabled(False)
        self.exp_stop.setEnabled(True)
        self._append_log(f"stats-exporter: listening on {bind}:{port}")

    def _on_exporter_stop(self) -> None:
        exporter, self.state.stats_exporter = self.state.stats_exporter, None
        if exporter is not None:
            try:
                exporter.stop()
            except Exception:  # noqa: BLE001
                logger.exception("stop exporter")
        self._update_exporter_status(running=False)
        self.exp_start.setEnabled(True)
        self.exp_stop.setEnabled(False)
        self._append_log("stats-exporter: stopped")

    def _update_exporter_status(self, running: bool) -> None:
        if running and self.state.stats_exporter is not None:
            port = self.state.stats_exporter.bound_port or int(self.exp_port.value())
            bind = getattr(self.state.stats_exporter, "bind", "127.0.0.1")
            self.exp_status_value.setText(
                i18n.t("send.expose_status_on").format(bind=bind, port=port)
            )
        else:
            self.exp_status_value.setText(i18n.t("send.expose_status_off"))

    def _refresh_exporter_endpoint_hint(self) -> None:
        port = int(self.exp_port.value())
        if self.exp_bind_net.isChecked():
            # Network mode: show the first local IPv4 so the URL is usable
            # from another host.
            v4 = local_addresses(AddressFamily.IPV4)
            host = v4[0] if v4 else "127.0.0.1"
        else:
            host = "127.0.0.1"
        self.exp_endpoint_value.setText(f"http://{host}:{port}/stats")

    def _refresh_exporter_provider(self) -> None:
        """Update the exporter's stats provider to point at the current sender."""
        if self.state.stats_exporter is None:
            return
        # The provider closure references `self.state.sender` dynamically,
        # so the exporter picks up the new sender automatically. Nothing
        # to rebind here -- but we re-update the status text just in case.
        self._update_exporter_status(running=True)

    def _make_stats_provider(self):
        state = self.state
        family_marker = lambda: (state.sender.config.family.value if state.sender else "")
        target_count_for_mode = lambda s: (
            0 if s.config.mode is SenderMode.CONTINUOUS else int(s.config.count)
        )

        def provider() -> dict:
            sender = state.sender
            if sender is None:
                return {
                    "status": "idle",
                    "sent": 0, "bytes": 0, "pps": 0.0, "bps": 0.0,
                    "elapsed_sec": 0.0,
                    "target_group": "", "target_port": 0,
                    "target_family": "", "mode": "", "target_count": 0,
                }
            snap = sender.stats.snapshot()
            return {
                "status": "running",
                "sent": snap.total_packets,
                "bytes": snap.total_bytes,
                "pps": round(snap.pps, 2),
                "bps": round(snap.bps, 2),
                "elapsed_sec": round(snap.elapsed_sec, 2),
                "target_group": sender.config.group,
                "target_port": sender.config.port,
                "target_family": family_marker(),
                "mode": sender.config.mode.value,
                "target_count": target_count_for_mode(sender),
            }
        return provider

    # -- shutdown -------------------------------------------------------- #

    def shutdown(self) -> None:
        if self.state.sender is not None and self.state.sender.is_running():
            self.state.sender.stop()
        if self.state.stats_exporter is not None:
            try:
                self.state.stats_exporter.stop()
            except Exception:  # noqa: BLE001
                pass
            self.state.stats_exporter = None


# --------------------------------------------------------------------------- #
# Main window                                                                 #
# --------------------------------------------------------------------------- #


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.state = AppState()
        self.bus = _SignalBus()
        self._language_actions: dict[str, QAction] = {}

        self.tabs = QTabWidget()
        self.recv_tab = ReceiveTab(self.state, self.bus)
        self.send_tab = SendTab(self.state, self.bus)
        self.tabs.addTab(self.recv_tab, i18n.t("tab.receive"))
        self.tabs.addTab(self.send_tab, i18n.t("tab.send"))
        self.setCentralWidget(self.tabs)

        # Status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        sb.showMessage(i18n.t("status.ready"))

        # Menus
        self._build_menus()

        # Stats refresh timer
        self._refresh = QTimer(self)
        self._refresh.setInterval(500)
        self._refresh.timeout.connect(self.recv_tab.refresh_stats)
        self._refresh.start()

        # React to language changes
        i18n.add_listener(self._on_language_changed)
        # Apply initial translations (window title, menu, status bar, etc.)
        self.retranslate_ui()

    def _build_menus(self) -> None:
        mbar = self.menuBar()
        # File
        m_file = mbar.addMenu(i18n.t("menu.file"))
        act_quit = QAction(i18n.t("act.quit"), self)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)
        # View -> Language
        m_view = mbar.addMenu(i18n.t("menu.view"))
        m_lang = m_view.addMenu(i18n.t("menu.language"))
        for code, name in i18n.LANGUAGES.items():
            act = QAction(name, self, checkable=True)
            act.setData(code)
            act.setChecked(i18n.get_language() == code)
            act.triggered.connect(lambda _checked=False, c=code: self._switch_language(c))
            m_lang.addAction(act)
            self._language_actions[code] = act
        # Help
        m_help = mbar.addMenu(i18n.t("menu.help"))
        act_about = QAction(i18n.t("act.about"), self)
        act_about.triggered.connect(self._show_about)
        m_help.addAction(act_about)

    def _switch_language(self, lang: str) -> None:
        if lang == i18n.get_language():
            return
        i18n.set_language(lang)
        for code, act in self._language_actions.items():
            act.setChecked(code == lang)
        # Re-apply translations
        self.retranslate_ui()

    def _on_language_changed(self, _lang: str) -> None:
        # Called by the i18n module via add_listener. Re-translate UI.
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(i18n.t("app.title"))
        self.tabs.setTabText(0, i18n.t("tab.receive"))
        self.tabs.setTabText(1, i18n.t("tab.send"))
        self.recv_tab.retranslate_ui()
        self.send_tab.retranslate_ui()
        # Menus: rebuild the top-level entries that depend on language
        # (text only; shortcut objects are recreated by re-adding actions)
        for menu in self.menuBar().findChildren(type(self.menuBar().actions()[0].menu())):
            pass  # Qt's QMenuBar rebuilds text via setTitle on each QMenu
        mbar = self.menuBar()
        for top in mbar.findChildren(type(mbar.actions()[0].menu())):
            pass
        # The simplest robust approach: rebuild menus
        for action in list(mbar.actions()):
            mbar.removeAction(action)
        self._build_menus()
        self.statusBar().showMessage(i18n.t("status.ready"))

    def _show_about(self) -> None:
        QMessageBox.about(
            self, i18n.t("about.title"), i18n.t("about.body")
        )

    def closeEvent(self, ev) -> None:  # noqa: N802
        try:
            self.recv_tab.save_settings()
        except Exception:  # noqa: BLE001
            logger.exception("save settings")
        for row in list(self.state.receivers.values()):
            try:
                row.receiver.stop()
            except Exception:  # noqa: BLE001
                logger.exception("stop receiver")
        self.state.receivers.clear()
        if self.state.remote_poller is not None:
            try:
                self.state.remote_poller.stop()
            except Exception:  # noqa: BLE001
                logger.exception("stop poller")
            self.state.remote_poller = None
        self.send_tab.shutdown()
        super().closeEvent(ev)
