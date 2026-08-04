"""Launcher: ``python run.py`` to start the multicast test tool."""

from __future__ import annotations

import logging
import sys

from multicast_tool.qt_compat import QApplication, exec_app
from multicast_tool.ui import MainWindow


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Multicast Test Tool")
    app.setOrganizationName("multicast-tool")
    win = MainWindow()
    win.show()
    return exec_app(app)


if __name__ == "__main__":
    sys.exit(main())
