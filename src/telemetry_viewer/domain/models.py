"""Qt-free data model. Channel samples retain their own measurement times."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


@dataclass
class QualityIssue:
    line: int
    code: str
    message: str
    source: str = ""


@dataclass
class SourceFile:
    path: Path
    start_row: int
    end_row: int  # Exclusive index in the combined session.


@dataclass
class Channel:
    name: str
    unit: str
    source: str
    group: str
    samples: FloatArray
    time_s: FloatArray
    valid: NDArray[np.bool_]
    frequency_hz: float | None = None
    interpolation: Literal["none", "linear", "hold"] = "none"


@dataclass
class SessionMetadata:
    timezone: str = ""  # Empty means unspecified; raw logger times are always naive.
    acceleration_unit: str = "g (assumed)"
    gyroscope_unit: str = "deg/s (assumed)"
    orientation: str = "Unspecified sensor axes"
    time_correction: str = "Point correction: max(raw elapsed, previous + 1 microsecond)"
    gps_detection: str = "Changes in coordinates or speed; identical consecutive fixes undetectable"


@dataclass
class Session:
    filename: str
    row_index: NDArray[np.int64]
    nr: NDArray[np.int64]
    timestamp_raw: list[str]
    elapsed_time_s: FloatArray
    channels: dict[str, Channel]
    gps_fix_samples: NDArray[np.int64]
    gps_segment_start: NDArray[np.bool_]
    distance_gps_m: FloatArray
    distance_speed_m: FloatArray | None
    issues: list[QualityIssue] = field(default_factory=list)
    metadata: SessionMetadata = field(default_factory=SessionMetadata)
    distance_method: str = "speed"
    source_files: list[SourceFile] = field(default_factory=list)
    source_lines: NDArray[np.int64] | None = None

    def source_for_sample(self, sample: int) -> SourceFile | None:
        for source in self.source_files:
            if source.start_row <= sample < source.end_row:
                return source
        return None

    @property
    def distance_m(self) -> FloatArray:
        if self.distance_method == "speed" and self.distance_speed_m is not None:
            return self.distance_speed_m
        return self.distance_gps_m

    def axis(self, mode: str) -> FloatArray:
        if mode not in ("time", "distance"):
            raise ValueError("Axis must be time or distance")
        return self.elapsed_time_s if mode == "time" else self.distance_m

    def channel_axis(self, channel: Channel, mode: str) -> FloatArray:
        if mode == "time":
            return channel.time_s
        return np.interp(channel.time_s, self.elapsed_time_s, self.distance_m)

    def sample_at(self, x: float, mode: str, active: int = 0) -> int:
        axis = self.axis(mode)
        i = int(np.searchsorted(axis, x))
        i = min(i, len(axis) - 1)
        if i and abs(axis[i - 1] - x) < abs(axis[i] - x):
            i -= 1
        # Distance may plateau at stops. Keep the active time when ambiguous.
        lo, hi = (
            np.searchsorted(axis, axis[i], side="left"),
            np.searchsorted(axis, axis[i], side="right"),
        )
        return int(np.clip(active, lo, hi - 1))

    def fix_for_sample(self, sample: int) -> int | None:
        """Nearest existing accepted GPS fix in time; no manufactured measurements."""
        if not len(self.gps_fix_samples):
            return None
        times = self.elapsed_time_s[self.gps_fix_samples]
        t = self.elapsed_time_s[sample]
        i = min(int(np.searchsorted(times, t)), len(times) - 1)
        if i and abs(times[i - 1] - t) <= abs(times[i] - t):
            i -= 1
        return int(self.gps_fix_samples[i])

    def sample_for_location(self, lat: float, lon: float, active: int) -> int | None:
        from telemetry_viewer.processing.gps import haversine_m

        rows = self.gps_fix_samples
        if not len(rows) or not np.isfinite([lat, lon]).all():
            return None
        distances = haversine_m(
            lat, lon, self.channels["GPS_LAT"].samples[rows], self.channels["GPS_LON"].samples[rows]
        )
        # Within a 5 m geographic ambiguity band, prefer the active time fragment.
        candidates = rows[distances <= distances.min() + 5.0]
        return int(
            candidates[
                np.argmin(abs(self.elapsed_time_s[candidates] - self.elapsed_time_s[active]))
            ]
        )
