# Logger format and processing contract

UTF-8/ASCII (optional BOM), semicolon delimiter, decimal point; LF/CRLF are accepted.
Required columns: `NR`, `DATA` (`YYYY-MM-DD`), `CZAS` (`HH:MM:SS.mmm`, microseconds also accepted).
Optional known channels:

| Columns | Initial unit | Group |
| --- | --- | --- |
| ACC_X / ACC_Y / ACC_Z | g (assumed; configurable label) | Acceleration |
| GYRO_X / GYRO_Y / GYRO_Z | deg/s (assumed; configurable label) | Gyroscope |
| GPS_LAT / GPS_LON | decimal WGS84 degrees | GPS |
| GPS_SPEED_KMH | km/h | GPS |

Every additional numeric column is automatically retained as a channel with an unspecified
unit in CAN / Other. Unknown units are not inferred from numerical magnitude.

## Acquisition order and time

`row_index` is the retained zero-based acquisition position; `NR` is the original logger sequence
number. Rows remain in file order. Non-increasing NR is diagnosed, never sorted. An invalid NR
is flagged and represented by -1. `timestamp_raw` preserves the DATA + CZAS string, with no zone
conversion. Parseable dates preserve midnight crossings using the explicit calendar date.

`elapsed_time_s` starts at zero relative to the first parseable timestamp. Duplicates,
backsteps and invalid later times use `max(raw_elapsed, previous_display + 1e-6)`.
Invalid timestamps retain their samples. If the first timestamp is invalid, the first parseable
time is the reference; earlier/later anomalous rows are flagged and advanced as required.
All display-time differences are positive. Corrections do not reconstruct lost acquisition time.
Gaps >2 s are reported, not compressed. Elapsed time is a **presentation axis**; raw time is retained.

Malformed field-count rows are skipped with a physical file-line diagnostic; blank rows are
reported. Numeric parse failures, NaN, infinity and negative GPS speed are masked invalid without
dropping structurally usable rows. Empty/unsupported headers and unusable files produce a clear
error. Missing optional columns produce warnings; missing required columns prevent import.

## GPS and distances with variable update rates

No fixed IMU, GPS coordinate or GPS speed rate is assumed. The current flat logger format
repeats the latest values. An observable GPS update is a change in `(lat, lon, speed)`.
Speed-only updates are retained and add zero geodesic displacement. Identical consecutive frames
cannot be distinguished from copied values: the displayed count is **changes**, not hardware fixes.
Explicit fix timestamps/flags will belong in a future importer, not an arbitrary rate constant.

- Coordinates must be finite, latitude within ±90 and longitude within ±180 degrees.
- Haversine uses mean Earth radius 6,371,008.8 m (spherical approximation).
- A positional jump faster than 100 m/s is rejected. The interval is measured since the last
  accepted **coordinate** change, not the last speed-only update. Missing/invalid coordinate
  rows split the route; the unobserved gap contributes no geodesic distance.
- `distance_gps_m` accumulates only accepted GPS changes and holds between them. Repeated
  coordinates do not multiply the distance. It is a step axis, even if speed changes faster.
- `distance_speed_m` integrates previous-row GPS speed /3.6 over actual corrected time intervals
  (left rectangle, zero-order hold). Invalid speed contributes zero for that interval.
  A long recording gap holds the latest valid row speed until the next row and is warned about.
- `distance_m` defaults to the continuous speed integral where any usable speed exists;
  otherwise it uses GPS geodesic steps. The toolbar can switch methods without import.
  With neither usable speed nor GPS, the distance axis remains zero; Time stays usable.

The GPS marker is the nearest accepted change in time. Map clicks search accepted fixes by
geodesic distance; candidates within 5 m of the spatial minimum prefer the active time.
A stopped or repeated path is ambiguous by nature. Distance plateaus retain the active row
if it lies on the selected plateau; they cannot uniquely encode time.

## Multi-rate channel semantics

A `Channel` has its own `time_s`, samples, validity, unit, source and optional rate/interpolation
metadata. The legacy format references the shared row timeline; the row density is assigned
only as a nominal recorded-row rate for non-GPS channels, not proven sensor frequency.
GPS frequency is left unspecified. Future importers can supply genuinely independent arrays.

Plots draw source samples, using min/max peak downsampling only for rendering. Mapping an
independent channel time onto session distance linearly evaluates the **axis**, not channel
measurements. Readout uses the nearest recorded sample (and labels its timestamp). Channels
explicitly marked `hold` render as steps. No resampled measurements are added to the session.

CSV uses the shared session rows and writes values only at exactly matching measurement times;
other entries remain blank. Headers include units; `.csv.metadata.json` stores units, sources,
interpolation policy, time correction and timezone description. CSV retains raw timestamps,
row_index, NR, elapsed seconds and the selected distance method.

GPX 1.1 contains accepted fixes in separate track segments. Original naive timestamps and row
indices use a custom extension. Standard GPX `<time>` is omitted when timezone is unspecified.
When an IANA timezone is explicitly configured, valid times are converted to UTC; ambiguous or
nonexistent DST times fail export with a request to clear/resolve the zone. Neither exporter
modifies the source file. GUI prevents choosing the input as the export destination.

## Folder import

`LegacyTxtImporter.load` accepts either a file or a folder. Only immediate files with a
case-insensitive `.txt` suffix are included. Numeric filename groups are compared as integers,
left to right; equal numeric keys use case-insensitive name followed by the original name.
Unnumbered TXT filenames follow all numbered files in alphabetical order. Each file has its own
header, so optional column order and channel presence may differ. The union of numeric channels
is retained; absent values get NaN with invalid masks, not interpolated or carried measurements.

Parsing accumulates rows in this exact file order, without NR/timestamp sorting or automatic
deduplication. A reset of NR across files is reported and preserved. Time correction, GPS-state
change detection and distance integration run once over the combined raw timeline, keeping
normal recording continuity and detecting time anomalies at file boundaries. Existing long-gap
speed-hold semantics remain in effect; a folder does not imply independent time-zero sessions.

Session `source_files` records each resolved input path and its half-open row range; `source_lines`
retains the physical line for every accepted row. Quality issues carry a source basename. Exports
include basenames and line numbers, without exposing absolute source paths in metadata.
A structurally unusable/empty file stops the whole import with its filename. No partial session is
installed in the UI and no input file is modified. The file list is captured when importing;
this operation does not watch for files created later by an active logger.
