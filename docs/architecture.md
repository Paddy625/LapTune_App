# Architecture

`domain/models.py` defines Qt-free Session, Channel and metadata. NumPy arrays preserve full
precision and validity masks. `processing/` owns time correction, Haversine validation and
zero-order-held speed integration. `importers/base.py` is the importer protocol; the dedicated
legacy importer handles text diagnostics and builds a session. `exporters/files.py` exposes
CSV/GPX functions and the future exporter protocol.

`gui/` uses Qt Widgets and PyQtGraph. A QObject worker in QThread imports data and emits
progress/result/error. File parsing never runs in the GUI event loop. Closing during import
requests interruption; it never destroys a running QThread. Channel selection controls only
plot assignment; plotting depends on the Channel contract rather than a specific signal name.
All panels link their X range. Peak downsampling preserves visible extrema without changing
source arrays. Mouse events are rate-limited to 30 Hz. Cursor readout uses binary search and
does not recreate curves. Identical GPS marker updates are suppressed.

`maps/` packages Leaflet CSS/JS and license. A local HTML page owns the polyline and marker;
QWebChannel carries click coordinates to Session's mapping functions. JSON serialization
carries route coordinates to JavaScript. Channel values and logger text are never injected as
JavaScript. The page disallows navigation to external main-frame pages. Online tiles may fail
independently of route rendering. QWebEngine runs in a memory-only profile.

QSettings stores local preferences and recent paths. Orientation and units are metadata labels,
not numerical transformations. Application data, exported files and preferences are never
committed automatically. Private log data belongs in ignored `data/local/`.

CI separates source tests from Windows packaging. PyInstaller's WebEngine hooks collect Qt
helpers/resources; an explicit frozen self-check checks files and actually opens the packaged
GUI. A successful build alone is not treated as a runtime pass. Source and frozen smoke use a
synthetic fixture and Offline mode, so tile service availability cannot mask bridge failures.
The frozen packaging check is prepared but still needs an accessible Windows CI run.

For a new logger format, implement the Importer protocol and produce Channel arrays with
proper acquisition times and explicit validity. For CAN state signals choose hold semantics;
for analog resampling an importer/processing stage must explicitly opt into linear interpolation.
Future CAN hardware acquisition and DBC decoding belong in separate adapters/services and must
not become Qt widget or legacy-text parser logic. A format selector/registry can be added when
a second concrete importer exists.


Folder imports share the legacy parser with single-file imports. Discovery orders numeric
filename groups before parsing, then the parser builds a union of channels and source row ranges.
Time and distance processing runs only once for the combined session. The worker's result signal
installs a complete session atomically; any file error leaves the current session visible.
Source-file ranges also protect every input from export overwrites. No external dependency or
server is required for folder discovery, concatenation or numeric filename ordering.


Rendering reduction is configured on PlotItem because adding a PlotDataItem overrides its
constructor's downsampling options. The factor is recalculated from binary-searched visible
sample counts, so nonuniform time, gaps and distance plateaus do not rely on a global mean rate.
Time axes and grid items use raster caches; vertical-axis vector labels remain unclipped.
The application keeps Qt's pixmap cache at least 64 MiB.
Masked source values are cached for the current session and cleared when another session loads.
Shared channel timelines reuse the source distance array; independent timelines still map by time.

The scene has one mouse proxy shared by all four panels. Source-file and GPS cursor lookups
use binary search without gathering whole arrays. The map omits repeated consecutive coordinates
from the rendered route and isolates its Canvas renderer from the marker's SVG renderer.
See performance.md for measurements and the scope of the synthetic million-sample check.

The toolbar places folder import before single-file import. A prepared adaptation of the
user-supplied LapTune wordmark fills the toolbar height at its right edge. Its PNG is included
in wheel package data and the PyInstaller spec; source and frozen GUI smoke checks require it.


## Per-plot configuration and unit scales

Each plotted series is keyed by `(panel_index, channel_name)`, so a measurement may appear in
multiple panels while sharing its source arrays and cached validity mask. Channels with the
same unit share a ViewBox; each additional unit gets an independent linked ViewBox and a colored
right-hand AxisItem. All share X geometry/ranges and cursor positions. Additional right-axis
columns are reserved equally across visible panels. Unknown units use channel-specific groups.
The plot's minimum width includes its axis columns and a usable data area; docks yield space
on smaller screens. The analysis toolbar overflows before the 60-pixel branding toolbar does.

XOnlyViewBox directs wheel/drag gestures, including gestures over auxiliary layers, to X only.
All Y auto-range flags are disabled; PlotItem's native auto-range buttons/menus are replaced by
explicit per-plot options. Initial/new-unit ranges come from full-resolution valid data. Existing
ranges stay fixed across X zoom/pan, channel toggles, fits and time/distance switching. The dialog
or the explicit Fit Y action is the only way to revise an existing range.

AxisItem's Qt item cache can clip sibling Y labels with auxiliary ViewBoxes. Vertical axes are
painted directly from their cached vector pictures; a separate cached GridItem draws horizontal
lines. Time-axis pictures are rasterized after the first viewport paint, with clock tick spacing
based on the full hh:mm:ss text width and enough margin for edge labels. This avoids replaying the
entire grid on cursor movement. The synthetic mixed-axis benchmark is recorded in benchmarks/.

PlotConfigurationDialog edits titles, channel selection and per-unit bounds atomically. The
Channels panel has multi-plot assignment menus. QSettings key plot_layout_v2 stores titles,
channel names and explicit bounds, never measurement arrays. Metadata changes preserve those
assignments. CSV export deduplicates selected channel names across plots. The pure processing
helpers display.py and scales.py format elapsed durations and calculate valid data ranges.
