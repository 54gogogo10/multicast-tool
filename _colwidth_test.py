"""Verify the receive-table column-width feature.

Covers the Group-column stretch bug:
  1. Group column is Interactive (its right border is draggable).
  2. When the user has not manually resized Group, the table auto-fills
     the leftover viewport width into the Group column.
  3. Once the user drags Group, its width is locked and the auto-fill
     no longer overrides it -- even if the user resizes the window.
  4. "Reset Column Widths" re-enables the auto-fill.
  5. Saved widths survive a window close + reopen.

Uses the real QSettings scope so we exercise the same path as production.
The settings are cleared at start so prior runs don't poison the test.
"""

from __future__ import annotations

import sys

from multicast_tool.qt_compat import QApplication, QCoreApplication, QHeaderView, QSettings

from multicast_tool.ui import MainWindow


def main() -> int:
    QCoreApplication.setOrganizationName("multicast-tool")
    QCoreApplication.setApplicationName("Multicast Test Tool")
    s = QSettings("multicast-tool", "Multicast Test Tool")
    s.clear()
    s.sync()

    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()
    rt = w.recv_tab
    hdr = rt.table.horizontalHeader()
    ncols = len(rt.HEADER_KEYS)

    # ---- 1. all columns are Interactive (the fix) ----------------------
    for i in range(ncols):
        mode = hdr.sectionResizeMode(i)
        assert mode == QHeaderView.Interactive, f"col {i} mode={mode} expected Interactive"
    print("OK: all columns Interactive (right border of Group is draggable)")

    # ---- 2. auto-fill on first show -----------------------------------
    vp = rt.table.viewport().width()
    others = sum(hdr.sectionSize(i) for i in range(ncols) if i != 1)
    vh = rt.table.verticalHeader()
    vh_w = vh.width() if vh is not None and vh.isVisible() else 0
    expected_group = max(40, vp - others + vh_w)
    got_group = hdr.sectionSize(1)
    assert abs(got_group - expected_group) <= 2, f"Group={got_group} expected ~{expected_group}"
    print(f"OK: Group auto-fills to {got_group}px (viewport={vp}px)")

    # ---- 3. user dragging Group locks the width -----------------------
    hdr.resizeSection(1, 123)
    app.processEvents()
    assert rt._group_user_resized, "user_resized flag should be True after drag"
    w.resize(700, 600)
    app.processEvents()
    w.resize(1100, 760)
    app.processEvents()
    assert hdr.sectionSize(1) == 123, f"Group should stay at 123, got {hdr.sectionSize(1)}"
    print("OK: Group width locked after user drag (survives window resize)")

    # ---- 4. save / restore roundtrip ----------------------------------
    rt.save_settings()
    s.sync()
    w.close()
    app.processEvents()

    w2 = MainWindow()
    w2.show()
    app.processEvents()
    rt2 = w2.recv_tab
    hdr2 = rt2.table.horizontalHeader()
    app.processEvents()
    got = hdr2.sectionSize(1)
    assert abs(got - 123) <= 1, f"restored Group = {got}, expected 123"
    assert rt2._group_user_resized, "user_resized flag should be restored"
    print(f"OK: Group width 123 restored after restart (got {got})")

    # ---- 5. Reset Column Widths re-enables auto-fill ------------------
    rt2._on_reset_columns_clicked()
    app.processEvents()
    assert not rt2._group_user_resized, "user_resized flag should clear after reset"
    w2.resize(1400, 800)
    app.processEvents()
    vp2 = rt2.table.viewport().width()
    others2 = sum(hdr2.sectionSize(i) for i in range(ncols) if i != 1)
    vh2 = rt2.table.verticalHeader()
    vh2_w = vh2.width() if vh2 is not None and vh2.isVisible() else 0
    expected2 = max(40, vp2 - others2 + vh2_w)
    got2 = hdr2.sectionSize(1)
    assert abs(got2 - expected2) <= 2, f"after reset, Group={got2} expected ~{expected2}"
    print(f"OK: Reset re-enables auto-fill (Group -> {got2}px at viewport={vp2}px)")

    # ---- 6. reset survives a restart (QSettings bool regression) -------
    # The saved flag is stored as the string "true"/"false" on Windows;
    # bool("false") is True in Python, which used to re-lock the auto-fill
    # after every restart. Guard: flag must stay False, and auto-fill must
    # still own the Group column, after save + close + reopen.
    rt2.save_settings()
    s.sync()
    w2.close()
    app.processEvents()

    w3 = MainWindow()
    w3.show()
    app.processEvents()
    rt3 = w3.recv_tab
    hdr3 = rt3.table.horizontalHeader()
    app.processEvents()
    assert not rt3._group_user_resized, (
        f"user_resized flag must stay False after reset+restart, got {rt3._group_user_resized!r}"
    )
    w3.resize(1400, 800)
    app.processEvents()
    vp3 = rt3.table.viewport().width()
    others3 = sum(hdr3.sectionSize(i) for i in range(len(rt3.HEADER_KEYS)) if i != 1)
    vh3 = rt3.table.verticalHeader()
    vh3_w = vh3.width() if vh3 is not None and vh3.isVisible() else 0
    expected3 = max(40, vp3 - others3 + vh3_w)
    got3 = hdr3.sectionSize(1)
    assert abs(got3 - expected3) <= 2, f"after restart, Group={got3} expected ~{expected3}"
    print(f"OK: reset survives restart (auto-fill Group -> {got3}px at viewport={vp3}px)")

    s.clear()
    s.sync()
    w3.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
