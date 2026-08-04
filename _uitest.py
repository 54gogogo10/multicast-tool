"""Headless UI smoke-test: open the main window then quit immediately.

Run with: python _uitest.py
Returns 0 if the window opened and the event loop ran without errors.
"""
from __future__ import annotations

import sys

from multicast_tool.qt_compat import QApplication, QTimer, exec_app
from multicast_tool.ui import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()

    # Force a real paint to ensure no widget crashes at construction
    app.processEvents()

    # Schedule a clean shutdown 1.5s later so the refresh timer ticks once
    QTimer.singleShot(1500, win.close)
    rc = exec_app(app)
    print(f"UI exited with code {rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
