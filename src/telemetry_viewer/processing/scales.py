"""Unit grouping and full-resolution ranges for independent plot axes."""

import math

import numpy as np


def channel_unit(channel):
    # Unknown units must not silently share a scale with other unrelated unknown signals.
    return channel.unit or f"{channel.name} (unit unspecified)"


def full_y_range(channels):
    low, high = math.inf, -math.inf
    for channel in channels:
        if not len(channel.samples) or not channel.valid.any():
            continue
        data = channel.samples if channel.valid.all() else channel.samples[channel.valid]
        low = min(low, float(np.min(data)))
        high = max(high, float(np.max(data)))
    if not math.isfinite(low):
        return -1.0, 1.0
    pad = (high - low) * 0.05 if high > low else max(abs(low) * 0.05, 0.01)
    return low - pad, high + pad
