"""Derived signals on actual measurement times; source arrays are never modified."""

import math
import re
from dataclasses import asdict, dataclass

import numpy as np

from telemetry_viewer.domain.models import Channel

OPERATIONS = {
    "lowpass": "Low-pass filter (RC)",
    "highpass": "High-pass / remove slow component (RC)",
    "scale": "Gain and offset: gain × signal + offset",
    "convert": "Convert units",
    "magnitude": "Vector magnitude (2 or 3 axes)",
    "difference": "Difference: A − B",
    "derivative": "Time derivative",
    "integral": "Time integral (trapezoidal)",
}
UNIT_FACTORS = {
    "g": ("acceleration", 9.80665),
    "m/s²": ("acceleration", 1.0),
    "km/h": ("speed", 1 / 3.6),
    "m/s": ("speed", 1.0),
    "deg/s": ("angular velocity", math.pi / 180),
    "rad/s": ("angular velocity", 1.0),
}


def normalized_unit(unit):
    unit = unit.replace(" (assumed)", "").strip()
    return {"m/s^2": "m/s²", "m/s2": "m/s²", "°/s": "deg/s", "kmh": "km/h"}.get(unit, unit)


def conversion_factor(source, target):
    source, target = normalized_unit(source), normalized_unit(target)
    if source not in UNIT_FACTORS or target not in UNIT_FACTORS:
        raise ValueError("Supported conversions: g ↔ m/s², km/h ↔ m/s, deg/s ↔ rad/s.")
    group, factor = UNIT_FACTORS[source]
    target_group, target_factor = UNIT_FACTORS[target]
    if group != target_group:
        raise ValueError("Source and target units must describe the same physical quantity.")
    return factor / target_factor


@dataclass(frozen=True)
class SignalRecipe:
    name: str
    operation: str
    sources: tuple[str, ...]
    unit: str = ""
    gain: float = 1.0
    offset: float = 0.0
    cutoff_hz: float = 5.0
    zero_phase: bool = True
    max_gap_s: float = 1.0


def valid_runs(times, valid, max_gap_s):
    """Invalid rows, non-increasing clocks and long gaps break processing state."""
    starts = valid & np.r_[True, ~valid[:-1]]
    ends = valid & np.r_[~valid[1:], True]
    breaks = (np.diff(times) <= 0) | (np.diff(times) > max_gap_s)
    starts[1:] |= valid[1:] & breaks
    ends[:-1] |= valid[:-1] & breaks
    return zip(np.flatnonzero(starts), np.flatnonzero(ends) + 1, strict=True)


def _lowpass(values, times, cutoff, progress):
    result = np.empty_like(values)
    result[0] = values[0]
    coefficients = -np.expm1(-2 * math.pi * cutoff * np.abs(np.diff(times)))
    previous = values[0]
    for i in range(1, len(values)):
        if i % 4096 == 0:
            progress()
        previous += coefficients[i - 1] * (values[i] - previous)
        result[i] = previous
    return result


def validate_recipe(channels, recipe: SignalRecipe):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,79}", recipe.name):
        raise ValueError("Use a channel name with letters, digits and underscores (max. 80).")
    if recipe.name in channels:
        raise ValueError("This channel name already exists. Choose a new name.")
    if recipe.operation not in OPERATIONS:
        raise ValueError("Unknown signal operation.")
    expected = (
        (2, 3)
        if recipe.operation == "magnitude"
        else ((2,) if recipe.operation == "difference" else (1,))
    )
    if len(recipe.sources) not in expected or any(name not in channels for name in recipe.sources):
        raise ValueError("Select the required source channels.")
    if len(set(recipe.sources)) != len(recipe.sources):
        raise ValueError("Select different channels for vector components or a difference.")
    if not math.isfinite(recipe.max_gap_s) or recipe.max_gap_s <= 0:
        raise ValueError("The maximum processing gap must be positive.")
    sources = [channels[name] for name in recipe.sources]
    first = sources[0]
    if any(normalized_unit(c.unit) != normalized_unit(first.unit) for c in sources):
        raise ValueError(
            "Vector components and differences must use the same units. Convert first."
        )
    if recipe.operation == "convert":
        conversion_factor(first.unit, recipe.unit)
    if recipe.operation == "scale" and not np.isfinite([recipe.gain, recipe.offset]).all():
        raise ValueError("Gain and offset must be finite.")
    if recipe.operation in ("lowpass", "highpass") and (
        not math.isfinite(recipe.cutoff_hz) or recipe.cutoff_hz <= 0
    ):
        raise ValueError("Filter cutoff frequency must be positive.")
    return sources


def calculate_signal(channels, recipe: SignalRecipe, progress=lambda: None):
    sources = validate_recipe(channels, recipe)
    first = sources[0]
    times = first.time_s
    for channel in sources[1:]:
        if not np.array_equal(times, channel.time_s):
            times = np.intersect1d(times, channel.time_s)
    if not len(times):
        raise ValueError(
            "Source channels have no shared measurement times; no resampling is assumed."
        )
    arrays, valid = [], np.ones(len(times), dtype=bool)
    for channel in sources:
        if times is channel.time_s:
            data, mask = channel.samples, channel.valid
        else:
            indices = np.searchsorted(channel.time_s, times)
            data, mask = channel.samples[indices], channel.valid[indices]
        arrays.append(data)
        valid &= mask & np.isfinite(data) & np.isfinite(times)
    data, unit = arrays[0], first.unit
    operation = recipe.operation
    progress()
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        if operation == "scale":
            result, unit = recipe.gain * data + recipe.offset, recipe.unit or unit
        elif operation == "convert":
            result, unit = data * conversion_factor(unit, recipe.unit), normalized_unit(recipe.unit)
        elif operation == "magnitude":
            result = np.hypot(arrays[0], arrays[1])
            if len(arrays) == 3:
                result = np.hypot(result, arrays[2])
        elif operation == "difference":
            result = arrays[0] - arrays[1]
        else:
            result = np.full(len(times), np.nan)
            for lo, hi in valid_runs(times, valid, recipe.max_gap_s):
                progress()
                values, clock = data[lo:hi], times[lo:hi]
                if operation in ("lowpass", "highpass"):
                    filtered = _lowpass(values, clock, recipe.cutoff_hz, progress)
                    if recipe.zero_phase:
                        filtered = _lowpass(
                            filtered[::-1], clock[::-1], recipe.cutoff_hz, progress
                        )[::-1]
                    result[lo:hi] = filtered if operation == "lowpass" else values - filtered
                elif operation == "derivative" and hi - lo >= 2:
                    result[lo:hi] = np.gradient(values, clock)
                elif operation == "integral":
                    result[lo] = 0
                    result[lo + 1 : hi] = np.cumsum(
                        (values[:-1] + values[1:]) * 0.5 * np.diff(clock)
                    )
            if operation == "derivative":
                unit = "m/s²" if unit == "m/s" else f"({unit})/s"
            elif operation == "integral":
                unit = "m" if unit == "m/s" else f"({unit})·s"
    valid &= np.isfinite(result)
    result = np.where(valid, result, np.nan)
    if not valid.any():
        raise ValueError(
            "The result has no valid samples. Check sources and processing parameters."
        )
    details = asdict(recipe)
    details["source_units"] = [channel.unit for channel in sources]
    if operation in ("lowpass", "highpass"):
        description = f"{operation} RC, {recipe.cutoff_hz:g} Hz, " + (
            "forward/backward (zero phase)" if recipe.zero_phase else "causal"
        )
    else:
        description = OPERATIONS[operation]
    return Channel(
        recipe.name,
        unit,
        f"Calculated: {description}; from {', '.join(recipe.sources)}",
        "Calculated",
        result,
        times,
        valid,
        calculation=details,
    )


def channel_statistics(channel, axis=None, bounds=None):
    lo, hi = 0, len(channel.time_s)
    if bounds is not None:
        lo = int(np.searchsorted(axis, bounds[0], side="left"))
        hi = int(np.searchsorted(axis, bounds[1], side="right"))
    values = channel.samples[lo:hi]
    values = values[channel.valid[lo:hi] & np.isfinite(values)]
    if not len(values):
        return {"count": 0, **dict.fromkeys(("min", "max", "mean", "rms", "std", "peak_to_peak"))}
    return {
        "count": len(values),
        "min": float(values.min()),
        "max": float(values.max()),
        "mean": float(values.mean()),
        "rms": float(np.sqrt(np.mean(values * values))),
        "std": float(values.std()),
        "peak_to_peak": float(np.ptp(values)),
    }
