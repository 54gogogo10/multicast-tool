"""Qt binding detection and re-export.

Test scripts and downstream callers can do::

    from multicast_tool.qt_compat import QtCore, QtGui, QtWidgets, PYSIDE_VERSION

instead of writing the same try/except import block against
``PySide6`` / ``PySide2`` everywhere.

The widget classes used by the app are re-exported with the same names
they have in the corresponding ``PySideX.QtWidgets`` module, so the
rest of the codebase can use unqualified names.
"""

from __future__ import annotations

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt, QCoreApplication, QTimer, QByteArray, QEvent, QSettings, Signal, QObject
    from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
    from PySide6.QtWidgets import (
        QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
        QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
        QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
        QPushButton, QSpinBox, QStatusBar, QTableWidget, QTableWidgetItem,
        QTabWidget, QVBoxLayout, QWidget,
    )
    PYSIDE_VERSION = 6
except ImportError:  # PySide2 / Win7 build
    from PySide2 import QtCore, QtGui, QtWidgets  # noqa: F401
    from PySide2.QtCore import Qt, QCoreApplication, QTimer, QByteArray, QEvent, QSettings, Signal, QObject
    from PySide2.QtGui import QColor, QFont, QKeySequence
    from PySide2.QtWidgets import (
        QAbstractItemView, QAction, QApplication, QCheckBox, QComboBox,
        QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
        QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
        QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QStatusBar,
        QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
    )
    PYSIDE_VERSION = 2

__all__ = [
    "QtCore", "QtGui", "QtWidgets",
    "Qt", "QCoreApplication", "QTimer", "QByteArray", "QEvent", "QSettings", "Signal", "QObject",
    "QAction", "QColor", "QFont", "QKeySequence",
    "QAbstractItemView", "QApplication", "QCheckBox", "QComboBox", "QDoubleSpinBox",
    "QFormLayout", "QGridLayout", "QGroupBox", "QHBoxLayout", "QHeaderView",
    "QLabel", "QLineEdit", "QMainWindow", "QMessageBox", "QPlainTextEdit",
    "QProgressBar", "QPushButton", "QSpinBox", "QStatusBar", "QTableWidget",
    "QTableWidgetItem", "QTabWidget", "QVBoxLayout", "QWidget",
    "PYSIDE_VERSION", "exec_app",
]


def exec_app(app):
    """Compatibility wrapper for ``QApplication.exec`` / ``exec_``.

    Qt 5 (PySide2) renamed the method to ``exec_`` and added a deprecated
    ``exec`` shim; Qt 6 (PySide6) dropped ``exec_`` entirely. This helper
    picks the right one.
    """
    if PYSIDE_VERSION == 6:
        return app.exec()
    return app.exec_()
