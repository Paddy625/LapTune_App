# Executed validation

Development host: Linux, Python 3.12, NumPy 2.5.4, PySide6 6.12.0, PyQtGraph 0.14.0.
Versions used by the source/test workflow are constrained in `constraints.txt`.

- `pytest -q`: **71 passed**, including the optional private-log integration test.
  Public CI will skip that test when `data/local/LOG0023.TXT` is absent.
- `ruff check .`: passed; formatting is checked separately with `ruff format --check .`.
- GUI smoke: asynchronous import, original curve sample count, Time/Distance switching,
  local Leaflet route and marker, plot-to-map cursor and JavaScript-to-Python selection passed
  on the synthetic single-file and split-folder fixtures using Linux offscreen software rendering.
- Folder regression tests cover numeric order, mixed-case extensions, direct children only,
  absent channels, unchanged inputs, cross-file GPS deduplication/time/distance, source-aware
  diagnostics and exports, invalid-file errors, aggregate progress and cancellation.
- Local PyInstaller **Linux** onedir build and the frozen application's offline GUI smoke passed.
  This validates local-resource packaging on Linux, not Windows compatibility.
- The private input retained **18,640** samples over **180.272 s**, with **32** point time
  corrections and **181 observable GPS changes**. No physical-frame count is claimed.
  Speed-integrated distance: approximately **3746.277 m**; GPS geodesic: **3721.540 m**.
  No private route or timestamps are included in this repository or screenshot.
- `tools/benchmark_local.py data/local/LOG0023.TXT`: import plus initial render ~0.797 s;
  300 cursor updates p95 ~1.083 ms, max ~4.609 ms; curve objects were retained.
  The maximum initial UI timer gap was ~435 ms during startup/import/render; this is not
  a claim of uninterrupted sub-frame responsiveness during initial loading.
  Timings describe this host and one log, not a million-sample guarantee.
- The documentation screenshot was captured from the real application using
  `tools/capture_demo.py docs/synthetic-demo.png`. Its route and channels are generated.

The initial implementation's Windows source tests, frozen EXE/resource smoke and ZIP packaging
passed in [GitHub Actions](https://github.com/Paddy625/LapTune_App/actions/runs/41229959076).
This run predates folder support; check the workflow for the current commit for its Windows result.
Not executed locally: Windows 10/11 manual launch, online map/satellite tiles or a million-row TXT import benchmark.

For isolated Linux GUI checks, set `QT_QPA_PLATFORM=offscreen`,
`QT_QUICK_BACKEND=software`, `QT_OPENGL=software`,
`QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-dev-shm-usage"` and, only in an isolated
CI/container test, `QTWEBENGINE_DISABLE_SANDBOX=1`. The app does not set this flag itself.
Qt may report absent GPU/DBus in this headless environment; the assertions require actual
local page initialization and bidirectional WebChannel behavior regardless of those messages.


The earlier interactive rendering update passed 37 local tests, including new GUI regression checks on
186,400 synthetic samples with narrow spikes and nonuniform timestamps. Tests check actual
reduced point count, full source arrays, separate Leaflet renderers and zero static-route redraws
on marker movement, branding/action order and allocation-free GPS timeline lookup. Rendering
benchmarks were also executed on synthetic sessions of 186,400 and 1,000,000 samples; see
performance.md and docs/benchmarks/. These synthetic sessions bypass TXT parsing.
The screenshot was regenerated from synthetic data and shows the folder-first toolbar and logo.


The configurable-plot update passes 49 local tests. A real Qt wheel event verifies X zoom with
all three unit-specific Y ranges unchanged; dragging an auxiliary layer also pans shared X only.
Tests exercise the real configuration dialog and every header/menu target, range editing/refit,
duplicate channels across panels, restoration of saved layouts, nine channels/four units in the
fourth plot, CSV column deduplication, full-height branding and pixel checks for visible Y labels.
Both desktop and 1028×749 windows are checked: docks yield space to extra axes, titles remain
visible and the logo stays inside the toolbar. Clock tick spacing accounts for full label width.
Formatting tests include midnight-sized elapsed durations, sessions over 24 hours and millisecond
cursor values. Synthetic screenshots were regenerated from the application.

The mixed-axis rendering check on 186,400 constructed samples keeps six displayed series and
source arrays intact. In 90 interaction frames, p95 cursor updates were 0.747 ms, viewport paint
17.373 ms (including pan/zoom frames), steady cursor-only paint 5.293 ms and map round trips
3.222 ms. See docs/benchmarks/mixed-axis-rendering.json. This is a rendering check, not TXT import
throughput or a hardware-independent frame-rate guarantee.

GUI smoke tests, rendering checks and screenshot tools use temporary INI settings on all
platforms, including Windows. Running them does not overwrite the user's saved plot layout.

The signal/range update adds numerical checks for noise suppression on an uneven clock,
causal step response, invalid/gap resets, unit conversion, exact-time vector/difference
alignment, derivatives/integrals, cancellation, full-resolution statistics and CSV recipe
provenance. Route tests cover temporal snapping, segment-aware tangents, no GPS, stationary
fixes and distance plateaus.

Both desktop and small-window Qt checks now exercise the real signal dialog and worker,
Calculated tree category, choosing a filtered channel in a plot, statistics, removal of the
assignment column and persistence of 1/2/4 visible plots without automatic expansion. Real
Leaflet checks verify start/end rows in Time/Distance modes, fixed-size marker bars, a separate
renderer and zero static-route redraws on plot-range changes. Out-of-session bounds clear marks.

DC alignment checks cover positive/negative constants, invalid samples, empty/invalid reference
ranges, gain plus signed residual offset, unchanged source arrays, and CSV baseline provenance.
The desktop/small-window GUI checks verify hidden Source B/C fields and labels for all unary
operations, the correct B/C rows for difference/vector operations, and a real background DC
calculation calibrated from the viewport. Time and Distance views select the same reference
interval, and two visible plots remain selected after publication of the centered channel.
