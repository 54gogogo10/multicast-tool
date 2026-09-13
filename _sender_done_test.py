"""Verify send-tab state after a send finishes naturally.

Regression test for two related bugs:
  1. The stats-exporter provider reported a frozen "running" status after
     a BURST / RATE_LIMITED send completed on its own.
  2. The tab kept a reference to the finished sender, so the exporter
     never switched back to idle.

Run with: python _sender_done_test.py
"""

from __future__ import annotations

import sys
import time

from multicast_tool.core import (
    AddressFamily, MulticastSender, SenderConfig, SenderMode,
)
from multicast_tool.qt_compat import QApplication, QCoreApplication, QSettings
from multicast_tool.ui import MainWindow


def _wait_finished(sender: MulticastSender, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while sender.is_running() and time.monotonic() < deadline:
        time.sleep(0.02)
    return not sender.is_running()


def main() -> int:
    QCoreApplication.setOrganizationName("multicast-tool")
    QCoreApplication.setApplicationName("Multicast Test Tool")
    s = QSettings("multicast-tool", "Multicast Test Tool")
    s.clear(); s.sync()

    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()
    try:
        # ---- 1. provider reports idle for a finished-but-referenced sender --
        sender = MulticastSender(SenderConfig(
            family=AddressFamily.IPV4, group="239.255.0.88", port=37723,
            ttl=0, payload_size=32, mode=SenderMode.BURST, count=10,
        ))
        sender.start()
        assert _wait_finished(sender), "burst did not finish"
        w.state.sender = sender  # simulate the tab still holding the reference
        snap = w.send_tab._make_stats_provider()()
        assert snap["status"] == "idle", snap
        print("OK: provider returns idle for a finished sender")

        # ---- 2. natural completion clears state.sender via STOPPED --------
        sender2 = MulticastSender(SenderConfig(
            family=AddressFamily.IPV4, group="239.255.0.88", port=37724,
            ttl=0, payload_size=32, mode=SenderMode.BURST, count=10,
        ))
        # Same wiring the Send tab uses
        sender2.on_event = lambda ev: w.bus.sender_event.emit(ev)
        w.state.sender = sender2
        sender2.start()
        assert _wait_finished(sender2), "burst did not finish"
        deadline = time.monotonic() + 5.0
        while w.state.sender is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert w.state.sender is None, "STOPPED event did not clear state.sender"
        assert w.send_tab._make_stats_provider()()["status"] == "idle"
        print("OK: STOPPED event clears state.sender; exporter stays idle")
    finally:
        if w.state.sender is not None:
            w.state.sender.stop()
        w.close()
        app.processEvents()
        s.clear(); s.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
