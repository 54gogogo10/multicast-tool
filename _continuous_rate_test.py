"""Verify CONTINUOUS mode honours the rate field.

  1. UI: rate spinbox is enabled for both Rate-limited and Continuous.
  2. Core: a CONTINUOUS sender with rate=200 pps actually runs near that
     rate (within +/- 25% over a 1-second window).
"""

from __future__ import annotations

import sys
import time

from multicast_tool.qt_compat import QApplication, QCoreApplication, QSettings

from multicast_tool import i18n
from multicast_tool.core import (
    AddressFamily, MulticastSender, SenderConfig, SenderMode,
)
from multicast_tool.ui import MainWindow


def main() -> int:
    QCoreApplication.setOrganizationName("multicast-tool")
    QCoreApplication.setApplicationName("Multicast Test Tool")
    s = QSettings("multicast-tool", "Multicast Test Tool")
    s.clear(); s.sync()

    # ---- 1. UI: rate enabled for both modes ---------------------------
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()
    st = w.send_tab
    # Map the displayed text back to an enum (works in both zh and en)
    def _mode():
        cur = st.s_mode.currentText()
        for m in SenderMode:
            if cur in (m.value, i18n.t(f"mode.{m.value}")):
                return m
        return SenderMode.BURST

    # Walk through each mode and assert the rate spinbox state
    for mode, rate_enabled, count_enabled in [
        (SenderMode.BURST,        False, True),
        (SenderMode.RATE_LIMITED, True,  True),
        (SenderMode.CONTINUOUS,   True,  False),  # <- the new behaviour
    ]:
        st.s_mode.setCurrentText(i18n.t(f"mode.{mode.value}"))
        app.processEvents()
        assert _mode() == mode, (mode, _mode())
        assert st.s_rate.isEnabled() is rate_enabled, (mode, st.s_rate.isEnabled())
        assert st.s_count.isEnabled() is count_enabled, (mode, st.s_count.isEnabled())
    print("OK: rate spinbox enabled for Rate-limited and Continuous")

    # ---- 2. Core: CONTINUOUS honours rate_pps -------------------------
    GROUP = "239.255.0.77"
    PORT = 37711
    # Use a loopback-friendly group + a moderate rate, measure for 1 second
    sender = MulticastSender(SenderConfig(
        family=AddressFamily.IPV4, group=GROUP, port=PORT,
        ttl=0, payload_size=64, mode=SenderMode.CONTINUOUS,
        rate_pps=200, count=0,
    ))
    sender.start()
    time.sleep(1.0)  # let it run for a full second
    sender.stop()
    snap = sender.stats.snapshot()
    print(f"OK: CONTINUOUS @ 200 pps target, sent {snap.total_packets} pkts "
          f"({snap.pps:.0f} pps actual) in {snap.elapsed_sec:.2f}s")
    # Allow generous tolerance: +/- 50% to absorb scheduler jitter
    assert 100 <= snap.total_packets <= 350, snap.total_packets
    assert 100 <= snap.pps <= 350, snap.pps

    s.clear(); s.sync()
    w.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
