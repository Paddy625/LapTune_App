"""Offline load smoothing on continuous navigation runs; source arrays stay untouched."""

import numpy as np
from scipy.signal import butter, sosfiltfilt


def smooth_loads(time, loads, starts, cutoff_hz=5.0, max_gap_s=0.25, check=lambda: None):
    """Fourth-order SOS each way, with final -3 dB cutoff and invalid edge guards.

    Interpolate a temporary copy onto a uniform grid per run, then restore the original
    timestamps. GPS loss alone does not split IMU data. No state crosses invalid IMU,
    a recording gap or navigation reinitialization. Short runs remain unavailable.
    """
    if not np.isfinite(cutoff_hz) or cutoff_hz <= 0:
        raise ValueError("Load smoothing frequency must be finite and positive")
    time, loads = np.asarray(time), np.asarray(loads)
    result = np.full_like(loads, np.nan, dtype=float)
    valid = np.isfinite(time) & np.isfinite(loads).all(axis=1)
    breaks = np.r_[True, (np.diff(time) <= 0) | (np.diff(time) > max_gap_s)] | starts
    left = valid & np.r_[True, ~valid[:-1]]
    right = valid & np.r_[~valid[1:], True]
    left |= valid & breaks
    right[:-1] |= valid[:-1] & breaks[1:]
    details = {
        "type": "Butterworth SOS",
        "order_per_pass": 4,
        "phase": "forward/backward, zero phase",
        "cutoff_hz": cutoff_hz,
        "cutoff_definition": "-3 dB after both passes",
        "source_channels": ["CALC_G_LOAD_" + axis for axis in "XYZ"],
        "timebase": "navigation IMU clock; uniform temporary grid per continuous run",
        "edge_guard_s": 3 / cutoff_hz,
        "edge_policy": "three cutoff periods at each run boundary are unavailable",
        "filtered_runs": 0,
        "short_runs_skipped": 0,
        "undersampled_runs_skipped": 0,
    }
    for lo, hi in zip(np.flatnonzero(left), np.flatnonzero(right) + 1, strict=True):
        check()
        clock = time[lo:hi]
        if len(clock) < 16 or clock[-1] - clock[0] <= 2 * details["edge_guard_s"]:
            details["short_runs_skipped"] += 1
            continue
        fs = (len(clock) - 1) / (clock[-1] - clock[0])
        if cutoff_hz >= fs / 2:
            details["undersampled_runs_skipped"] += 1
            continue
        # Forward/backward squares the magnitude. Prewarp to put the combined
        # -3 dB point at the requested frequency rather than silently using -6 dB.
        correction = (np.sqrt(2) - 1) ** (1 / 8)
        design_cutoff = fs / np.pi * np.arctan(np.tan(np.pi * cutoff_hz / fs) / correction)
        sos = butter(4, design_cutoff, fs=fs, output="sos")
        grid = np.linspace(clock[0], clock[-1], len(clock))
        uniform = np.column_stack(
            [np.interp(grid, clock, loads[lo:hi, j]) for j in range(loads.shape[1])]
        )
        filtered = sosfiltfilt(sos, uniform, axis=0, padtype="odd", padlen=15)
        check()
        restored = np.column_stack(
            [np.interp(clock, grid, filtered[:, j]) for j in range(loads.shape[1])]
        )
        interior = (clock >= clock[0] + details["edge_guard_s"]) & (
            clock <= clock[-1] - details["edge_guard_s"]
        )
        result[lo:hi] = np.where(interior[:, None], restored, np.nan)
        details["filtered_runs"] += 1
    return result, details
