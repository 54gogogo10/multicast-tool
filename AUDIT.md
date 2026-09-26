# Audit Log — Round-4 series (post feature: loss detection / DSCP / CSV export)

Scope: full codebase re-review after adding three features
(sequence-based loss detection, DSCP/TOS marking, CSV export).
Ten focused audit rounds were performed; findings and dispositions below.
Verification scripts: `_feature_test.py` (feature behaviour) and
`_audit_round4_test.py` (this round's fixes, kept as regression pins).

## Round 1 — Core networking logic (core.py)

**F1-1 (major, fixed) — Phantom loss after reorder-hole-fill.**
The first SeqTracker draft measured circular sequence distance from the
"last received" sequence and bumped it on late packets, so after a gap +
late fill, subsequent in-order packets were double-counted as lost
(0..9 → 14 → 12 → 15 reported 6 lost instead of the real 3).
Rewritten to the standard accounting: track `max_seq` plus a 64-bit
receipt bitmap window. A late packet inside the window that was
previously counted lost now compensates the estimate (`lost -= 1`,
counted `out_of_order`); a late packet outside the window counts as a
stale duplicate and never touches loss. Circular-space distance keeps
32-bit wrap-around correct (verified through `0xFFFFFFFF → 0`).
Pinned by `_feature_test.py::test_2` and `_audit_round4_test.py::A/B`.

**F1-2 (minor, fixed) — `_build_payload` overshoot for tiny payloads.**
`payload_size < 20` produced a 20-byte buffer for an 8-byte config.
Truncation to `n` bytes (untracked `bytes`) restored the contract.

## Round 2 — Concurrency / threading

`SeqTracker` uses one lock; never nested with `StatsTracker`'s.
`_session_id` is written before `Thread.start()` (happens-before).
The sequence payload buffer is only touched by the sender thread; the
HTTP stats provider reads `state.sender` through a local reference.
Receiver-side `feed` (recv thread) vs `snapshot`/`reset` (GUI thread)
are both lock-guarded. No findings.

## Round 3 — Security

* CSV formula injection (`=`, `+`, `-`, `@`, tab, CR) — mitigated with a
  leading-apostrophe sanitiser in `stats.csv_safe_cell`; pinned by tests.
* Memory DoS via spoofed fresh session ids — bounded by
  `MAX_SEQ_STREAMS = 64` with least-recently-seen eviction; pinned by test.
* `unpack_from` over-read — impossible: length-checked before unpack.
* DSCP values masked `& 0x3F` before reaching `setsockopt`.
* **F3-1 (doc, fixed)** — trust model undocumented: any host on the
  group can forge `MCT1` headers and skew loss stats (UDP multicast has
  no authentication). Added an explicit *Trust model* note to README.

## Round 4 — UI logic

Column indexes re-based for the Loss column (10) / Elapsed (11); all new
widgets registered for runtime retranslation; persisted 11-column header
state restores tolerantly into 12 columns; `QFileDialog` is static-API
identical on Qt5/Qt6.
**F4-1 (minor, fixed)** — `refresh_stats` used `loss.lost if loss else 0`
(relying on dataclass truthiness). Made the `None` path explicit.

## Round 5 — Compatibility (Python 3.8 / PySide2)

All modules keep `from __future__ import annotations`; no 3.9+ syntax;
`QFileDialog` exported from both PySide6/PySide2 branches of
`qt_compat`; `struct.Struct.pack_into` on `bytearray` is 3.8-safe.
Full feature test + selftest pass under `venv-win7` (Python 3.8.10).

## Round 6 — Resource lifecycle

`write_csv` is context-managed; `SeqTracker` state is owned by its
receiver (no globals, no timers); senders/receivers that fail validation
never allocate sockets; the exporter lifecycle is unchanged from the
round-3 audited state. No findings.

## Round 7 — Input validation & error paths

DSCP range validated in `core` (0–63) and by spinbox range (0–63).
**F7-1 (minor, fixed)** — the CSV export handler caught only `OSError`;
`csv.Error` or an unexpected write failure would die silently in the Qt
slot. Broadened to `Exception` with `logger.exception` + error dialog.

## Round 8 — Performance

`SeqTracker.feed`: ~0.43 µs/call (≈2.3 M feeds/s). Per-packet header
rewrite (`pack_into` + `time.time()`): ≈6 M/s. Both are far above any
achievable `sendto` rate, so loss tracking does not throttle sending or
receiving. Full regression suite green on CPython 3.11 and 3.8.

## Round 9 — i18n coverage

Static scan of every `i18n.t(...)` key used in `ui.py` against
`STRINGS["zh_CN"]` / `STRINGS["en"]`: 0 missing; 130/130 keys in parity
(`_i18n_test.py` enforces the parity assertion on every run).

## Round 10 — Adversarial inputs (hostile packets)

Truncated headers, wrong magic, all-zero, non-tuple addresses and an
oversized valid-header payload were pushed through
`MulticastReceiver._track_seq`: no exception escapes to the receive
loop, tracking stays bounded. `write_csv` to a directory raises
`OSError` (and the UI handles it). Real-socket checks confirm DSCP
lands on IPv4 (`IP_TOS` readback `46<<2`) and the IPv6 `IPV6_TCLASS`
degrade path never raises on platforms without it.
All pinned in `_audit_round4_test.py`.

## Summary

| Round | Focus | Findings | Fixed |
|---|---|---|---|
| 1 | Core networking | F1-1 phantom loss (major), F1-2 tiny payload | 2 |
| 2 | Concurrency | — | — |
| 3 | Security | F3-1 trust-model doc | 1 |
| 4 | UI logic | F4-1 ambiguous None handling | 1 |
| 5 | 3.8/PySide2 compat | — | — |
| 6 | Resource lifecycle | — | — |
| 7 | Validation/error paths | F7-1 CSV error path | 1 |
| 8 | Performance | — | — |
| 9 | i18n coverage | — | — |
| 10 | Adversarial inputs | — | — |

Five findings, five fixed (including the major loss-accounting rewrite).
Regression pins live in `_feature_test.py` and `_audit_round4_test.py`.


## Addendum — Layout / occlusion round (post round-4 series)

A GUI occlusion audit (`_layout_audit.py`) was added: it runs the real
`MainWindow` at the declared minimum and default window sizes in both
languages and flags central/tab minimum-size overflow, clipped labels /
buttons, and compressed boxes. Findings (all compared against the HEAD
baseline to separate pre-existing issues from feature regressions):

| # | Finding | Disposition |
|---|---|---|
| L1 | Window minimum (1000x660) far below the layout's real minimum -- users could resize into clipped states | Fixed: minimums reduced, floor re-measured and declared as 1208x800 |
| L2 | Receive tab vertical overflow: table card compressed to ~0 body at min size | Fixed: table/log/tile minimum heights lowered (140/80/74 -> 96/56/68) and window minimum raised |
| L3 | Send-parameter form labels collapsed to zero width on narrow windows (English) | Fixed: labels bid their full text width as a minimum in retranslate_ui |
| L4 | Remote-sender panel: single control row could not hold English labels/buttons; stats grid clipped English labels | Fixed: split into address row + interval/buttons row; stats grid 2 pairs per row; panels share width equally |
| L5 | "Reset Counters (selected)" too wide for the button bar (EN) | Fixed: shortened to "Reset Counters" |

Verification: zero flagged issues at 1208x800 and 1280x820 with real
Windows font metrics, both languages, under BOTH PySide6/Qt6 and the
PySide2/Qt5 venv-win7 toolchain (Qt5 metrics run ~28px taller, which set
the final floor); screenshots reviewed. The audit script doubles as a
regression gate (exit 1 on any issue). Note: the offscreen QPA inflates
font metrics (especially English), so the gate runs the real platform on
Windows and offscreen elsewhere. All four PyInstaller artifacts
(onedir/onefile x PySide6/PySide2) were rebuilt from this source and
launch-smoked.
