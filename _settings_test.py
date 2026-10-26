"""Verify the usability features added around QSettings persistence.

  1. Fresh defaults: the receive form starts on 239.1.1.1 so a first-run
     loopback test pairs with the Send tab default without editing.
  2. Interface combo display strings ("ip (name)") resolve back to the
     plain IP the config layer expects.
  3. Form values (receive + send + remote monitor + exporter) and window
     geometry survive save + close + reopen.
  4. Language preference persists via i18n.load_saved_language.
  5. After a burst finishes: the last stats snapshot stays on the tiles,
     the status line shows the sent count, and progress reaches 100
     (STOPPED events now carry `sent`).

Run with: python _settings_test.py
"""
from __future__ import annotations

import sys
import time

from multicast_tool import i18n
from multicast_tool.core import AddressFamily, SenderMode, interface_entries
from multicast_tool.qt_compat import QApplication, QCoreApplication, QSettings
from multicast_tool.ui import MainWindow


def main() -> int:
    QCoreApplication.setOrganizationName("multicast-tool")
    QCoreApplication.setApplicationName("Multicast Test Tool")
    s = QSettings("multicast-tool", "Multicast Test Tool")
    s.clear()
    s.sync()

    # ---- 0. fresh defaults pair up -------------------------------------
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()
    rt, st = w.recv_tab, w.send_tab
    assert rt.group_edit.text() == "239.1.1.1", rt.group_edit.text()
    assert st.s_group.text() == "239.1.1.1", st.s_group.text()
    print("OK: fresh receive default (239.1.1.1) pairs with the send default")

    ents = interface_entries(AddressFamily.IPV4)
    assert isinstance(ents, list) and isinstance(ents[0], tuple), ents
    assert rt._iface_map, "interface combo map is empty"
    for disp, ip in rt._iface_map.items():
        assert rt._iface_value(disp) == ip, (disp, ip)
        assert "(" not in ip, ip
    print(f"OK: {len(rt._iface_map)} labelled interface entries resolve to plain IPs")

    # ---- 1. persistence roundtrip --------------------------------------
    rt.group_edit.setText("239.255.7.7")
    rt.port_edit.setValue(4747)
    rt.sources_edit.setText("10.1.1.1")
    rt.remote_addr_edit.setText("192.168.50.10:8765")
    rt.remote_interval.setValue(2.5)
    st.s_group.setText("239.255.8.8")
    st.s_port.setValue(4848)
    st.s_ttl.setValue(8)
    st.s_dscp.setValue(46)
    st.s_payload.setValue(512)
    st.s_count.setValue(7777)
    st.s_rate.setValue(250)
    st.s_template.setChecked(True)
    st.s_template_text.setText("probe")
    st.exp_port.setValue(9999)
    st.s_mode.setCurrentText(i18n.t("mode.rate"))
    app.processEvents()
    w.close()
    app.processEvents()
    s.sync()

    w2 = MainWindow()
    w2.show()
    app.processEvents()
    rt2, st2 = w2.recv_tab, w2.send_tab
    assert rt2.group_edit.text() == "239.255.7.7", rt2.group_edit.text()
    assert rt2.port_edit.value() == 4747
    assert rt2.sources_edit.text() == "10.1.1.1"
    assert rt2.remote_addr_edit.text() == "192.168.50.10:8765"
    assert abs(rt2.remote_interval.value() - 2.5) < 1e-6
    assert st2.s_group.text() == "239.255.8.8", st2.s_group.text()
    assert st2.s_port.value() == 4848
    assert st2.s_ttl.value() == 8
    assert st2.s_dscp.value() == 46
    assert st2.s_payload.value() == 512
    assert st2.s_count.value() == 7777
    assert st2.s_rate.value() == 250
    assert st2.s_template.isChecked() and st2.s_template_text.text() == "probe"
    assert st2.s_template_text.isEnabled()
    assert st2.exp_port.value() == 9999
    cur_mode = next(
        (m for m in SenderMode
         if st2.s_mode.currentText() in (m.value, i18n.t(f"mode.{m.value}"))),
        None,
    )
    assert cur_mode is SenderMode.RATE_LIMITED, st2.s_mode.currentText()
    assert abs(w2.width() - w.width()) <= 2 and abs(w2.height() - w.height()) <= 2, (
        w2.width(), w2.height(), w.width(), w.height())
    print("OK: form values (recv/send/remote/exporter) + geometry survive restart")

    # ---- 2. language preference persists -------------------------------
    i18n.set_language("en")
    assert i18n.load_saved_language() == "en"
    i18n.set_language("zh_CN")
    assert i18n.load_saved_language() == "zh_CN"
    print("OK: language preference persists")

    # ---- 3. send-tab final-stats retention -----------------------------
    st2.s_group.setText("239.255.9.9")
    st2.s_port.setValue(39391)
    st2.s_ttl.setValue(0)
    st2.s_payload.setValue(32)
    st2.s_mode.setCurrentText(i18n.t("mode.burst"))
    st2.s_count.setValue(10)
    app.processEvents()
    st2._on_start_clicked()
    deadline = time.monotonic() + 5.0
    while w2.state.sender is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert w2.state.sender is None, "burst send did not finish"
    assert st2._last_snap is not None, "final snapshot was not captured"
    assert st2._last_snap.total_packets == 10, st2._last_snap
    st2.refresh_stats()
    assert "10" in st2._tiles["send.tile.sent"][1].text()
    assert "10" in st2.s_status.text(), st2.s_status.text()
    assert st2.s_progress.value() == 100, st2.s_progress.value()
    print("OK: final stats retained after completion; done status + progress 100")

    s.clear()
    s.sync()
    w2.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
