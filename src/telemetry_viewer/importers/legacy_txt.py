import csv
from collections.abc import Callable
from pathlib import Path

import numpy as np

from telemetry_viewer.domain.models import Channel, QualityIssue, Session
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
}


class LegacyTxtImporter:
    def load(self, path: Path, progress: Callable[[int], None] | None = None) -> Session:
        path = Path(path)
        issues, raw, lines, nr = [], [], [], []
        size = max(path.stat().st_size, 1)
        consumed, last_progress = 0, -1
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
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
                values = {name: [] for name in numeric}
                for missing in KNOWN.keys() - set(header):
                    issues.append(
                        QualityIssue(1, "missing_channel", f"Optional column missing: {missing}")
                    )
                positions = {name: header.index(name) for name in header}
                for row in reader:
                    line = reader.line_num
                    consumed += sum(len(v) for v in row) + len(row) + 1
                    percent = min(95, int(95 * consumed / size))
                    if progress and percent != last_progress:
                        progress(percent)
                        last_progress = percent
                    if not row or all(not v.strip() for v in row):
                        issues.append(QualityIssue(line, "empty_row", "Blank row skipped"))
                        continue
                    if len(row) != len(header):
                        issues.append(
                            QualityIssue(
                                line,
                                "malformed_row",
                                f"Expected {len(header)} fields; got {len(row)}; skipped",
                            )
                        )
                        continue
                    try:
                        number = int(row[positions["NR"]])
                        if not -(2**63) <= number < 2**63:
                            raise ValueError
                    except ValueError:
                        number = -1
                        issues.append(QualityIssue(line, "invalid_nr", "Invalid NR retained as -1"))
                    if nr and number <= nr[-1]:
                        issues.append(
                            QualityIssue(
                                line, "nr_order", "NR not increasing; file order preserved"
                            )
                        )
                    nr.append(number)
                    raw.append(row[positions["DATA"]] + " " + row[positions["CZAS"]])
                    lines.append(line)
                    for name in numeric:
                        try:
                            value = float(row[positions[name]])
                            if not np.isfinite(value) or (name == "GPS_SPEED_KMH" and value < 0):
                                raise ValueError
                        except ValueError:
                            value = np.nan
                            issues.append(
                                QualityIssue(
                                    line, "invalid_number", f"{name}: invalid numeric value"
                                )
                            )
                        values[name].append(value)
        except UnicodeError as exc:
            raise ImportErrorDetail("Invalid text encoding; expected ASCII or UTF-8") from exc
        except csv.Error as exc:
            raise ImportErrorDetail(f"Malformed CSV: {exc}") from exc
        if not nr:
            raise ImportErrorDetail("No usable data rows after the header")
        time, time_issues = build_timebase(raw, lines)
        issues.extend(time_issues)
        frequency = (len(time) - 1) / time[-1] if len(time) > 1 else None
        channels = {}
        for name, data in values.items():
            samples = np.asarray(data, dtype=np.float64)
            unit, group = KNOWN.get(name, ("", "CAN / Other"))
            valid = np.isfinite(samples)
            if name in ("GPS_LAT", "GPS_LON"):
                valid &= abs(samples) <= (90 if name == "GPS_LAT" else 180)
            channels[name] = Channel(name, unit, "Logger", group, samples, time, valid, frequency)
        nan = np.full(len(time), np.nan)
        speed = channels["GPS_SPEED_KMH"].samples if "GPS_SPEED_KMH" in channels else nan
        if "GPS_LAT" in channels and "GPS_LON" in channels:
            fixes, starts, gps, gps_issues = gps_distances(
                channels["GPS_LAT"].samples, channels["GPS_LON"].samples, speed, time, lines
            )
            issues.extend(gps_issues)
        else:
            fixes, starts = np.array([], dtype=np.int64), np.array([], dtype=bool)
            gps = np.zeros(len(time))
        has_speed = np.isfinite(speed).any()
        speed_distance = integrate_speed(speed, time) if has_speed else None
        if not has_speed:
            issues.append(QualityIssue(1, "no_speed", "No usable speed; distance uses GPS steps"))
        if progress:
            progress(100)
        return Session(
            path.name,
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
        )
