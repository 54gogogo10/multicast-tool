"""Headless layout/occlusion audit.

Runs the real MainWindow offscreen at several window sizes / languages,
activates each tab in turn, and flags:

* CENTRAL-MIN-OVERFLOW -- central widget's minimumSizeHint exceeds the
  space the window actually gives it (content WILL be compressed/clipped);
* TAB-MIN-OVERFLOW     -- same per tab page;
* H-CLIP / V-CLIP      -- a visible QLabel / QPushButton / QGroupBox is
  smaller than its sizeHint / minimumSizeHint (text or frame cut off).

Also grabs PNGs of both tabs into _layout_shots/ (zh_CN only) for visual
review. Run: python _layout_audit.py
"""
import os
import sys

# The real platform is the authoritative check (offscreen font fallback
# inflates metrics, especially for English); offscreen keeps the script
# usable on headless non-Windows hosts.
if sys.platform.startswith("win"):
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
else:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from multicast_tool.qt_compat import (  # noqa: E402
    QApplication, QCoreApplication, QSettings, QGroupBox, QLabel, QPushButton,
)
from multicast_tool.ui import MainWindow  # noqa: E402


def _txt(x):
    return x.title() if isinstance(x, QGroupBox) else x.text()


def audit_tab(w, tab, tag, issues):
    w.tabs.setCurrentWidget(tab)
    app.processEvents()
    tms = tab.minimumSizeHint()
    if tms.height() > tab.height() + 1 or tms.width() > tab.width() + 1:
        issues.append(f"{tag}: TAB-MIN-OVERFLOW need {tms.width()}x{tms.height()} "
                      f"have {tab.width()}x{tab.height()}")
    widgets = (tab.findChildren(QLabel) + tab.findChildren(QPushButton)
               + tab.findChildren(QGroupBox))
    for wd in widgets:
        if not wd.isVisible() or not _txt(wd):
            continue
        # QGroupBox width is a soft constraint (the box compresses with its
        # content; the 'need' reflects body sizeHints, not the title), so
        # only QLabel / QPushButton participate in the hard H-CLIP check.
        if (not isinstance(wd, QGroupBox)
                and wd.width() < wd.sizeHint().width() - 2):
            issues.append(f"{tag}: H-CLIP {type(wd).__name__} "
                          f"'{_txt(wd)[:22]}' w={wd.width()} need={wd.sizeHint().width()}")
        if wd.height() < wd.minimumSizeHint().height() - 1:
            issues.append(f"{tag}: V-CLIP {type(wd).__name__} "
                          f"'{_txt(wd)[:22]}' h={wd.height()} "
                          f"need={wd.minimumSizeHint().height()}")


def run(lang, size, stag, issues, do_shots):
    from multicast_tool import i18n
    i18n.set_language(lang)
    w = MainWindow()
    w.show()
    w.resize(size[0], size[1])
    app.processEvents()
    cw = w.centralWidget()
    ms = cw.minimumSizeHint()
    if ms.width() > cw.width() + 1 or ms.height() > cw.height() + 1:
        issues.append(f"{lang}/{stag}: CENTRAL-MIN-OVERFLOW "
                      f"need {ms.width()}x{ms.height()} have {cw.width()}x{cw.height()}")
    for tname, tab in (("recv", w.recv_tab), ("send", w.send_tab)):
        audit_tab(w, tab, f"{lang}/{stag}/{tname}", issues)
        if do_shots:
            pix = w.grab()
            fname = f"{stag.rsplit('/', 1)[-1]}_{tname}.png"
            pix.save(os.path.join("_layout_shots", fname))
    w.close()
    app.processEvents()


def main():
    issues = []
    probe = MainWindow()
    probe.show()
    app.processEvents()
    wmin = probe.minimumSize()
    wdef = (max(probe.width(), 1280), max(probe.height(), 820))
    probe.close()
    app.processEvents()
    for lang in ("zh_CN", "en"):
        run(lang, (wmin.width(), wmin.height()), f"{lang}/min", issues,
            do_shots=(lang == "zh_CN"))
        run(lang, wdef, f"{lang}/std", issues, do_shots=False)
    QSettings("multicast-tool", "Multicast Test Tool").clear()
    print(f"==== {len(issues)} issue(s) at min={wmin.width()}x{wmin.height()} "
          f"default={wdef[0]}x{wdef[1]}, deduped below ====")
    seen = set()
    for s in issues:
        key = s.split(": ", 1)[-1]
        if key in seen:
            continue  # same geometry across languages
        seen.add(key)
        print(s)
    return 1 if issues else 0


QCoreApplication.setOrganizationName("multicast-tool")
QCoreApplication.setApplicationName("Multicast Test Tool")
QSettings("multicast-tool", "Multicast Test Tool").clear()
app = QApplication(sys.argv)
sys.exit(main())
