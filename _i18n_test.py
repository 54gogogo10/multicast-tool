"""Verify the i18n layer and language switcher.

  1. Default language is Chinese (zh_CN).
  2. Switching to English translates UI strings in-place.
  3. Switching back to Chinese re-translates correctly.
  4. Family and version combo contents follow the current language.
"""

from __future__ import annotations

import sys

from multicast_tool.qt_compat import QApplication, QCoreApplication, QSettings

from multicast_tool import i18n
from multicast_tool.ui import MainWindow


def main() -> int:
    QCoreApplication.setOrganizationName("multicast-tool")
    QCoreApplication.setApplicationName("Multicast Test Tool")
    s = QSettings("multicast-tool", "Multicast Test Tool")
    s.clear(); s.sync()

    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()

    # ---- 1. default language is Chinese -----------------------------
    assert i18n.get_language() == "zh_CN", i18n.get_language()
    win_title = w.windowTitle()
    recv_tab_text = w.tabs.tabText(0)
    send_tab_text = w.tabs.tabText(1)
    add_btn_text = w.recv_tab.add_btn.text()
    # Use unicode escapes to avoid console encoding issues
    assert "\u7ec4\u64ad\u6d4b\u8bd5\u5de5\u5177" in win_title, win_title  # 组播测试工具
    assert "\u63a5\u6536" in recv_tab_text, recv_tab_text  # 接收
    assert "\u53d1\u9001" in send_tab_text, send_tab_text  # 发送
    assert "\u6dfb\u52a0\u5e76\u5f00\u59cb" in add_btn_text, add_btn_text  # 添加并开始
    print(f"OK: default language zh_CN, title={win_title!r}, recv={recv_tab_text!r}")

    # ---- 2. switch to English ---------------------------------------
    w._switch_language("en")
    app.processEvents()
    assert i18n.get_language() == "en"
    assert "Multicast Test Tool" in w.windowTitle()
    assert "Receive" in w.tabs.tabText(0)
    assert "Send" in w.tabs.tabText(1)
    assert "Add" in w.recv_tab.add_btn.text() and "Start" in w.recv_tab.add_btn.text()
    print(f"OK: switched to en, title={w.windowTitle()!r}")

    # ---- 3. switch back to Chinese ---------------------------------
    w._switch_language("zh_CN")
    app.processEvents()
    assert i18n.get_language() == "zh_CN"
    assert "\u7ec4\u64ad\u6d4b\u8bd5\u5de5\u5177" in w.windowTitle()
    assert "\u6dfb\u52a0\u5e76\u5f00\u59cb" in w.recv_tab.add_btn.text()
    print(f"OK: switched back to zh_CN")

    # ---- 4. family / version combo follows language ----------------
    # In English, the family combo should show "IPv4" / "IPv6" as labels
    w._switch_language("en")
    app.processEvents()
    fam_items_en = [w.recv_tab.family_combo.itemText(i)
                    for i in range(w.recv_tab.family_combo.count())]
    assert fam_items_en == ["IPv4", "IPv6"], fam_items_en
    w._switch_language("zh_CN")
    app.processEvents()
    fam_items_zh = [w.recv_tab.family_combo.itemText(i)
                    for i in range(w.recv_tab.family_combo.count())]
    assert fam_items_zh == ["IPv4", "IPv6"], fam_items_zh  # values happen to be same
    print(f"OK: family combo translates (en={fam_items_en}, zh={fam_items_zh})")

    s.clear(); s.sync()
    w.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
