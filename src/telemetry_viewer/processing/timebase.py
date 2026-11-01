from datetime import datetime

import numpy as np

from telemetry_viewer.domain.models import QualityIssue


def estimate_missing_times(values, missing, lines):
    """Estimate only empty timestamps; never replace malformed or recorded timestamps."""
    issues = []
    recorded = np.flatnonzero(np.isfinite(values))
    if len(recorded) > 1:
        steps = np.diff(values[recorded]) / np.diff(recorded)
        adjacent = np.diff(recorded) == 1
        positive = steps[(steps > 0) & adjacent]
        if not len(positive):
            positive = steps[steps > 0]
        cadence = float(np.median(positive)) if len(positive) else None
    else:
        cadence = None
    starts = np.flatnonzero(missing & ~np.r_[False, missing[:-1]])
    ends = np.flatnonzero(missing & ~np.r_[missing[1:], False]) + 1
    for start, end in zip(starts, ends, strict=True):
        before = values[start - 1] if start else np.nan
        after = values[end] if end < len(values) else np.nan
        method = None
        if np.isfinite([before, after]).all() and after > before:
            values[start:end] = np.linspace(before, after, end - start + 2)[1:-1]
            method = "interpolated between recorded timestamps"
        elif start == 0 and np.isfinite(after) and cadence is not None:
            values[start:end] = after - cadence * np.arange(end - start, 0, -1)
            method = f"extrapolated using observed median row interval {cadence:.9g} s"
        elif end == len(values) and np.isfinite(before) and cadence is not None:
            values[start:end] = before + cadence * np.arange(1, end - start + 1)
            method = f"extrapolated using observed median row interval {cadence:.9g} s"
        issues.append(
            QualityIssue(
                lines[start],
                "estimated_time" if method else "missing_time",
                f"{end - start} consecutive empty timestamps: "
                + (method if method else "cannot estimate; monotonic display fallback used"),
            )
        )
    return issues


def build_timebase(raw: list[str], lines: list[int]) -> tuple[np.ndarray, list[QualityIssue]]:
    parsed: list[datetime | None] = []
    issues = []
    for value, line in zip(raw, lines, strict=True):
        try:
            parsed.append(datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f"))
        except ValueError:
            parsed.append(None)
            if value.strip():
                issues.append(
                    QualityIssue(line, "invalid_time", f"Invalid logger date/time: {value}")
                )
    origin = next((t for t in parsed if t is not None), None)
    values = np.array(
        [(t - origin).total_seconds() if t is not None and origin else np.nan for t in parsed]
    )
    missing = np.array([not value.strip() for value in raw], dtype=bool)
    if missing.any():
        issues.extend(estimate_missing_times(values, missing, lines))
        # A file can start before the first GPS clock lock. Keep those IMU samples
        # at their estimated spacing and start the session axis at the first row.
        if np.isfinite(values[0]):
            values -= values[0]
    elapsed = np.zeros(len(raw), dtype=np.float64)
    for i, value in enumerate(values):
        if i == 0:
            elapsed[i] = 0.0
        elif not np.isfinite(value) or value <= elapsed[i - 1]:
            elapsed[i] = elapsed[i - 1] + 1e-6
            issues.append(
                QualityIssue(lines[i], "time_correction", "Display time advanced by 1 µs")
            )
        else:
            elapsed[i] = value
        if i and elapsed[i] - elapsed[i - 1] > 2:
            issues.append(QualityIssue(lines[i], "time_gap", "Time gap > 2 s; speed held over gap"))
    return elapsed, issues
