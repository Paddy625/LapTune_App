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
