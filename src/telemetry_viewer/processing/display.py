"""Presentation of elapsed durations, without timezone or 24-hour wrapping."""

import math


def cursor_number(value: float | None) -> str:
    """Compact, numeric-only readout; retain a row for missing or invalid data."""
    if value is None or not math.isfinite(value):
        return "—"
    text = f"{value:.3f}"
    return text if len(text) <= 12 else f"{value:.6g}"


def elapsed_clock(value: float, milliseconds: bool = False) -> str:
    if not math.isfinite(value):
        return "—"
    sign = "-" if value < 0 else ""
    scale = 1000 if milliseconds else 1
    ticks = int(round(abs(value) * scale)) if milliseconds else math.floor(abs(value) + 1e-9)
    total_seconds, fraction = divmod(ticks, scale)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    result = f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
    return result + (f".{fraction:03d}" if milliseconds else "")
