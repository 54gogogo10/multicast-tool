# AGENTS.md — Multicast Test Tool (IGMP / MLD)

PySide GUI tool for joining IPv4/IPv6 multicast groups, sending multicast
traffic, and watching live receive stats. Entry point: `python run.py`.
`README.md` is thorough (protocol details, Win7 deployment, KB patches) —
read it before touching networking or build/packaging.

## Layout

- `run.py` — launcher.
- `multicast_tool/` — the whole app:
  - `core.py` — `MulticastReceiver` / `MulticastSender`, IGMP/MLD join
    helpers. **Intentionally UI-agnostic** — keep it that way; tests
    embed it directly.
  - `stats.py` — thread-safe `StatsTracker` (sliding-window pps/bps) + formatters.
  - `remote.py` — `StatsExporter` (sender-side HTTP `GET /stats` server) and
    `RemoteSenderPoller` (receiver-side poller).
  - `i18n.py` — all UI strings in `STRINGS[lang][key]` (zh_CN / en), `t(key)`.
  - `ui.py` — `MainWindow`, `ReceiveTab`, `SendTab`.
  - `theme.py` — design system: dark/light palettes + QSS builder
    (`apply_app` / `ensure_applied`), persisted in QSettings `ui/theme`.
  - `qt_compat.py` — PySide6/PySide2 shim.
- `build.bat` + `*.spec` — PyInstaller builds; `venv-win7/` + `wheels-win7/`
  are the pre-isolated Python 3.8 / PySide2 toolchain for the Win7 build.
- `_*.py` at repo root — headless self-test scripts (see below).

## Commands

No pytest, lint, or typecheck config. Tests are standalone assert-based
scripts that exit 0/1; run them with plain `python`:

```bash
python _selftest.py        # core / stats / multicast roundtrip (main test)
python _uitest.py          # window opens and closes
python _colwidth_test.py   # column resize + QSettings persistence
python _remote_test.py     # sender-rate sync end-to-end (HTTP /stats)
python _i18n_test.py       # language switcher
python _sender_done_test.py  # sender tab state after a send finishes
python _security_test.py   # remote.py host:port validation
```

Builds (Windows): `build.bat` (PySide6 onedir), `build.bat onefile`,
`build.bat win7` and `build.bat onefile-win7` (PySide2, require
`venv-win7`). Output in `dist\`; `build\` is a PyInstaller scratch dir.
`python _exe_smoke.py` / `_onefile_smoke.py` smoke-test built exes.

## Architecture rules

- **Qt imports only via `multicast_tool.qt_compat`** — never
  `from PySide6 import ...` outside that shim. The same source must build
  under PySide2 (Qt 5). Watch the diffs: in PySide2 `QAction` lives in
  `QtWidgets`, and `QApplication.exec` is `exec_` — use `exec_app(app)`.
- **core.py stays UI-agnostic.** Worker threads communicate via
  `ReceiverEvent` / `SenderEvent` callbacks; `ui.py` bridges them to the
  GUI thread through the `_SignalBus` Qt signals. Never touch widgets
  from a worker thread.
- **Every user-facing string goes through `i18n.t("key")`** and needs an
  entry for both `zh_CN` and `en` in `STRINGS`. After changing UI text,
  update the matching `retranslate_ui()` (language switches at runtime);
  `_i18n_test.py` checks that keys stay in sync.
- **Visual style lives in `theme.py`** — don't hardcode colors in ui.py.
  Dynamic rate colors must come from `theme.rate_color()`; for QLabels
  use ui.py's `_set_label_color` (palette-based — `QLabel.setForeground`
  does not exist in PySide6). The QSS deliberately sets no `color` for
  plain labels/table items so palette-based dynamic colors keep working.
- **Persistence** uses `QSettings("multicast-tool", "Multicast Test Tool")`
  (column widths, language). Keys are read at startup with defaults, so
  don't rename them casually.
- `remote.py` deliberately rejects URLs/schemes/paths/bracketed IPv6 in
  `validate_host_port()` (SSRF hardening); `_security_test.py` pins this
  behavior — don't loosen it without updating that test.

## Platform compatibility (hard constraint)

One source tree, two targets:

- **Win10+**: Python 3.11 + PySide6 (default).
- **Win7**: Python 3.8 + PySide2 5.15 (venv-win7, offline wheels).

So: no PySide6-only APIs, no Python >3.8 syntax at runtime (3.8-safe
type hints are fine because every module has
`from __future__ import annotations`). If in doubt, run
`_selftest.py` under `venv-win7\Scripts\python.exe` as well.
