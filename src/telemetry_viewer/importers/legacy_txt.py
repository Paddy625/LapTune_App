import csv
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np

from telemetry_viewer.domain.models import Channel, QualityIssue, Session, SourceFile
from telemetry_viewer.importers.base import ImportErrorDetail
from telemetry_viewer.processing.gps import gps_distances, integrate_speed
from telemetry_viewer.processing.timebase import build_timebase

KNOWN = {
    "ACC_X": ("g (assumed)", "Acceleration"),
    "ACC_Y": ("g (assumed)", "Acceleration"),
    "ACC_Z": ("g (assumed)", "Acceleration"),
    "GYRO_X": ("deg/s (assumed)", "Gyroscope"),
    "GYRO_Y": ("deg/s (assumed)", "Gyroscope"),
    "GYRO_Z": ("deg/s (assumed)", "Gyroscope"),
    "GPS_LAT": ("deg", "GPS"),
    "GPS_LON": ("deg", "GPS"),
    "GPS_SPEED_KMH": ("km/h", "GPS"),
    "GPS_ALT_MSL_M": ("m", "GPS"),
}
# Older firmware does not record altitude; its absence is not a data-quality issue.
NEW_OPTIONAL = {"GPS_ALT_MSL_M"}


def filename_order(path: Path) -> tuple:
    numbers = tuple(int(n) for n in re.findall(r"[0-9]+", path.stem))
    # Compare numbers before prefixes: LOG2 precedes LOG10, including unpadded names.
    # Names without numbers follow numbered files; equal numbers use a stable name tie-break.
    return not bool(numbers), numbers, path.name.casefold(), path.name


def folder_logs(path: Path) -> list[Path]:
    paths = sorted(
        (p for p in path.iterdir() if p.is_file() and p.suffix.casefold() == ".txt"),
        key=filename_order,
    )
    if not paths:
        raise ImportErrorDetail(f"{path.name}: no .txt files found in this folder")
    return paths


class LegacyTxtImporter:
    def load(self, path: Path, progress: Callable[[int], None] | None = None) -> Session:
        path = Path(path)
        paths = folder_logs(path) if path.is_dir() else [path]
        issues, raw, lines, nr, sources = [], [], [], [], []
        values: dict[str, list[float]] = {}
        sizes = [p.stat().st_size for p in paths]
        total_size = max(sum(sizes), 1)
        completed, last_progress = 0, -1

        def report(consumed):
            nonlocal last_progress
            percent = min(95, int(95 * consumed / total_size))
            if progress and percent > last_progress:
                progress(percent)
                last_progress = percent

        def flush_blank(name, blank_runs, source):
            if name in blank_runs:
                first, last, count = blank_runs.pop(name)
                issues.append(
                    QualityIssue(
                        first,
                        "missing_measurement",
                        f"{name}: {count} empty readings through line {last}; retained invalid",
                        source,
                    )
                )

        report(0)
        for source_path, size in zip(paths, sizes, strict=True):
            start_row = len(nr)
            consumed = 0
            source = source_path.name
            blank_runs = {}
            try:
                with source_path.open(encoding="utf-8-sig", newline="") as handle:
                    reader = csv.reader(handle, delimiter=";")
                    header = next(reader, None)
                    if not header:
                        raise ImportErrorDetail(
                            "Empty file: expected semicolon-separated header on line 1"
                        )
                    header = [name.strip() for name in header]
                    if len(header) < 2 or len(set(header)) != len(header) or "" in header:
                        raise ImportErrorDetail(
                            "Line 1: invalid header; expected unique columns separated by ;"
                        )
                    for required in ("NR", "DATA", "CZAS"):
                        if required not in header:
                            raise ImportErrorDetail(f"Line 1: missing required column {required}")
                    numeric = [n for n in header if n not in ("NR", "DATA", "CZAS")]
                    for name in numeric:
                        if name not in values:
                            values[name] = [np.nan] * start_row
                    for missing in sorted(KNOWN.keys() - NEW_OPTIONAL - set(header)):
                        issues.append(
                            QualityIssue(
                                1, "missing_channel", f"Optional column missing: {missing}", source
                            )
                        )
                    positions = {name: header.index(name) for name in header}
                    for row in reader:
                        line = reader.line_num
                        consumed += sum(len(v) for v in row) + len(row) + 1
                        report(completed + min(consumed, size))
                        if not row or all(not v.strip() for v in row):
                            issues.append(
                                QualityIssue(line, "empty_row", "Blank row skipped", source)
                            )
                            continue
                        if len(row) != len(header):
                            issues.append(
                                QualityIssue(
                                    line,
                                    "malformed_row",
                                    f"Expected {len(header)} fields; got {len(row)}; skipped",
                                    source,
                                )
                            )
                            continue
                        try:
                            number = int(row[positions["NR"]])
                            if not -(2**63) <= number < 2**63:
                                raise ValueError
                        except ValueError:
                            number = -1
                            issues.append(
                                QualityIssue(
                                    line, "invalid_nr", "Invalid NR retained as -1", source
                                )
                            )
                        if nr and number <= nr[-1]:
                            issues.append(
                                QualityIssue(
                                    line,
                                    "nr_order",
                                    "NR not increasing; file order preserved",
                                    source,
                                )
                            )
                        nr.append(number)
                        raw.append(row[positions["DATA"]] + " " + row[positions["CZAS"]])
                        lines.append(line)
                        for name, data in values.items():
                            if name not in positions:
                                data.append(np.nan)
                                continue
                            text = row[positions[name]].strip()
                            if not text:
                                first, _last, count = blank_runs.get(name, (line, line, 0))
                                blank_runs[name] = first, line, count + 1
                                data.append(np.nan)
                                continue
                            flush_blank(name, blank_runs, source)
                            try:
                                value = float(text)
                                if not np.isfinite(value) or (
                                    name == "GPS_SPEED_KMH" and value < 0
                                ):
                                    raise ValueError
                            except ValueError:
                                value = np.nan
                                issues.append(
                                    QualityIssue(
                                        line,
                                        "invalid_number",
                                        f"{name}: invalid numeric value",
                                        source,
                                    )
                                )
                            data.append(value)
                    for name in list(blank_runs):
                        flush_blank(name, blank_runs, source)
                if len(nr) == start_row:
                    raise ImportErrorDetail("No usable data rows after the header")
            except InterruptedError:
                raise
            except (UnicodeError, csv.Error, OSError, ImportErrorDetail) as exc:
                if isinstance(exc, UnicodeError):
                    message = "Invalid text encoding; expected ASCII or UTF-8"
                else:
                    message = str(exc)
                raise ImportErrorDetail(f"{source}: {message}") from exc
            sources.append(SourceFile(source_path.resolve(), start_row, len(nr)))
            completed += size
            report(completed)

        # Compute once across all files: preserve real timestamps, cross-file integration,
        # and repeated GPS states. Never concatenate independently zeroed session axes.
        row_indices = list(range(len(nr)))
        time, time_issues = build_timebase(raw, row_indices)
        frequency = (len(time) - 1) / time[-1] if len(time) > 1 else None
        channels = {}
        for name, data in values.items():
            samples = np.asarray(data, dtype=np.float64)
            unit, group = KNOWN.get(name, ("", "CAN / Other"))
            valid = np.isfinite(samples)
            if name in ("GPS_LAT", "GPS_LON"):
                valid &= abs(samples) <= (90 if name == "GPS_LAT" else 180)
            channels[name] = Channel(
                name,
                unit,
                "Logger",
                group,
                samples,
                time,
                valid,
                None if group == "GPS" else frequency,
            )
        nan = np.full(len(time), np.nan)
        speed = channels["GPS_SPEED_KMH"].samples if "GPS_SPEED_KMH" in channels else nan
        if "GPS_LAT" in channels and "GPS_LON" in channels:
            altitude = channels.get("GPS_ALT_MSL_M")
            fixes, starts, gps, gps_issues = gps_distances(
                channels["GPS_LAT"].samples,
                channels["GPS_LON"].samples,
                speed,
                time,
                row_indices,
                altitude=altitude.samples if altitude is not None else None,
            )
        else:
            fixes, starts = np.array([], dtype=np.int64), np.array([], dtype=bool)
            gps, gps_issues = np.zeros(len(time)), []
        ends = [s.end_row for s in sources]
        for issue in time_issues + gps_issues:
            source_index = int(np.searchsorted(ends, issue.line, side="right"))
            issue.source = sources[source_index].path.name
            issue.line = lines[issue.line]
            issues.append(issue)
        has_speed = np.isfinite(speed).any()
        speed_distance = integrate_speed(speed, time) if has_speed else None
        if not has_speed:
            issues.append(QualityIssue(1, "no_speed", "No usable speed; distance uses GPS steps"))
        if progress:
            progress(100)
        name = f"{path.name} ({len(paths)} files)" if path.is_dir() else path.name
        session = Session(
            name,
            np.arange(len(nr)),
            np.array(nr, dtype=np.int64),
            raw,
            time,
            channels,
            fixes,
            starts,
            gps,
            speed_distance,
            issues,
            distance_method="speed" if has_speed else "gps",
            source_files=sources,
            source_lines=np.array(lines, dtype=np.int64),
        )
        if any(issue.code == "estimated_time" for issue in time_issues):
            session.metadata.time_correction += (
                "; empty timestamps interpolated between recorded times, edge gaps extrapolated "
                "using the observed median row interval; estimates are not recorded clock values"
            )
        return session
