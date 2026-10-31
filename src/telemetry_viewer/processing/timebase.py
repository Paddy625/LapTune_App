from datetime import datetime

import numpy as np

from telemetry_viewer.domain.models import QualityIssue


def build_timebase(raw: list[str], lines: list[int]) -> tuple[np.ndarray, list[QualityIssue]]:
    parsed: list[datetime | None] = []
    issues = []
    for value, line in zip(raw, lines, strict=True):
        try:
            parsed.append(datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f"))
        except ValueError:
            parsed.append(None)
            issues.append(QualityIssue(line, "invalid_time", f"Invalid logger date/time: {value}"))
    origin = next((t for t in parsed if t is not None), None)
    elapsed = np.zeros(len(raw), dtype=np.float64)
    for i, t in enumerate(parsed):
        value = (t - origin).total_seconds() if t is not None and origin else np.nan
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
