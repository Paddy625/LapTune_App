# Interactive rendering performance

The old curve constructor requested automatic downsampling, but PlotItem.addItem reset that
option to the panel's disabled default. A zoomed panel still painted about 30,000 source points
per channel. Cursor updates also repainted all GPS route vertices because the marker and route
shared one Leaflet canvas.

The viewer now:

- Sets peak min/max reduction at the panel level and adapts the factor to the **actual count**
  of visible source samples and viewport width. It does not assume uniformly spaced time or
  distance. Zooming into a short interval restores the original measurement detail.
- Clips curves to the view, uses thin cosmetic pens, caches rasterized time axes/grid and keeps Qt's
  pixmap cache at least 64 MiB to avoid cache eviction on HiDPI screens.
- Reuses source arrays when valid, caches masked values across selection/axis changes, and
  uses one shared 30 Hz mouse proxy. Cursor readout and export use full-resolution channels.
- Finds nearest GPS fixes and source files using acquisition-index binary searches; moving
  the cursor no longer creates a full copy of all GPS times or scans the source-file list.
- Draws the static route with Canvas and the moving marker with a separate SVG renderer.
  Consecutive identical geographic vertices are omitted only from the displayed polyline;
  all recorded fix changes, raw samples, distances and exports remain intact.

## Measured results

Linux headless software rendering, 1560 × 920 window, Python 3.12 / Qt 6.12, 90 interaction
frames. Four default visible channels. A pan/zoom changes the visible range every 15 frames.
Each frame performs a cursor update, a synchronous Qt viewport repaint, then a WebEngine
JavaScript round trip. This includes actual paint cost, rather than timing only set_cursor.

| p95 metric | Before, 186,400 samples | After, 186,400 samples | After, 1,000,000 samples |
| --- | ---: | ---: | ---: |
| Python cursor update | 0.971 ms | 0.683 ms | 0.699 ms |
| Viewport paint, including pan/zoom frames | 82.841 ms | 28.042 ms | 23.401 ms |
| Viewport paint, cursor-only frames | not recorded | 4.798 ms | 4.456 ms |
| Map round trip after marker update | 75.511 ms | 3.551 ms | 3.065 ms |

The 186,400-sample session has ten source-file ranges; the million-sample session has 54.
Both sessions are **constructed from synthetic NumPy arrays**, not read from that many TXT
files. These results validate interaction/rendering with those session sizes, not million-row
file parsing time, sustained frame rate, long-run memory usage or performance on all hardware.
Raw results are in `docs/benchmarks/`. The initial GUI installation timings are not directly
comparable to whole-app import/startup because session construction happens before measurement.

Reproduce on a desktop with the development environment installed:

```powershell
.\.venv\Scripts\python.exe tools\benchmark_rendering.py --samples 186400 --frames 90
.\.venv\Scripts\python.exe tools\benchmark_rendering.py --samples 1000000 --frames 90
```

The benchmark uses only synthetic coordinates and Offline mode. Linux CI/headless flags are
documented in validation.md. Automated GUI regression tests enforce a bounded displayed point
count, preservation of narrow spikes and original arrays, correct reduction with a long timestamp
gap, distinct map renderers, zero route redraws on marker movement, action ordering and a valid
logo asset. They avoid unreliable wall-clock performance thresholds in CI.


## Configurable mixed-unit plots

Per-unit ViewBoxes reuse the same source arrays and adapt their reduced point counts to the
shared viewport. Y limits stay fixed, so X gestures never trigger repeated Y auto-ranging.
Vertical axis labels use vector pictures to avoid Qt's multi-axis item-cache clipping, while
horizontal grid lines have their own cached item. The full-height logo remains separate from
the plot scene.

`docs/benchmarks/mixed-axis-rendering.json` records a 186,400-sample, 90-frame check with ACC_X,
GYRO_Z and speed together in plot 1 and the default ACC/gyro plots below (six series total).
p95 steady repaint is 5.293 ms; including deliberate pan/zoom frames it is 17.373 ms. These
measurements use the same headless host methodology, with constructed data rather than disk I/O.
Reproduce with `tools/benchmark_rendering.py --samples 186400 --frames 90 --mixed`. The benchmark
uses temporary settings so it does not overwrite the user's saved plot layout.
