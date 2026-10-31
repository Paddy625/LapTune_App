# Executed validation

Development host: Linux, Python 3.12, NumPy 2.5.4, PySide6 6.12.0, PyQtGraph 0.14.0.
Versions used by the source/test workflow are constrained in `constraints.txt`.

- `pytest -q`: **22 passed**, including the optional private-log integration test.
  Public CI will skip that test when `data/local/LOG0023.TXT` is absent.
- `ruff check .`: passed; formatting is checked separately with `ruff format --check .`.
- GUI smoke: asynchronous import, original curve sample count, Time/Distance switching,
  local Leaflet route and marker, plot-to-map cursor and JavaScript-to-Python selection passed
  on the synthetic fixture using Linux offscreen software rendering.
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

Not executed: Windows source/frozen smoke, Windows 10/11 manual launch, online map tiles,
licensed satellite tiles, or 1M-sample benchmark. Source CI and Windows onedir ZIP jobs are
provided, including required WebEngine-resource checks and a packaged-EXE smoke test.
Git push was rejected with HTTP 403; GitHub API requests were also denied. No successful
Actions run, pull request, published branch or Windows download is claimed.

For isolated Linux GUI checks, set `QT_QPA_PLATFORM=offscreen`,
`QT_QUICK_BACKEND=software`, `QT_OPENGL=software`,
`QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-dev-shm-usage"` and, only in an isolated
CI/container test, `QTWEBENGINE_DISABLE_SANDBOX=1`. The app does not set this flag itself.
Qt may report absent GPU/DBus in this headless environment; the assertions require actual
local page initialization and bidirectional WebChannel behavior regardless of those messages.
