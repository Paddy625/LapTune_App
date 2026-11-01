"""Navigation inputs are separate from the preserved plotting timeline and raw GPS fixes."""

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from telemetry_viewer.processing.gps import EARTH_RADIUS_M, haversine_m


@dataclass
class NavigationClock:
    time: np.ndarray
    quality: np.ndarray  # 0: hardware clock; 1: logger date/time; 2: estimated/repaired
    warnings: list[str]
    interrupted: np.ndarray


def channel_values(session, name):
    channel = session.channels.get(name)
    if channel is None:
        return np.full(len(session.nr), np.nan)
    if channel.time_s is not session.elapsed_time_s:
        raise ValueError(f"{name} must contain original logger rows, not resampled values")
    return np.where(channel.valid, channel.samples, np.nan)


def navigation_clock(session, allow_estimated=True):
    n = len(session.nr)
    interrupted = np.zeros(n, dtype=bool)
    raw = np.full(n, np.nan)
    for i, text in enumerate(session.timestamp_raw):
        try:
            raw[i] = (
                datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f") - datetime(2000, 1, 1)
            ).total_seconds()
        except (ValueError, OSError, OverflowError):
            pass
    # Integer NR differences preserve known missing acquisitions. NR resets are not negative dt.
    counts = np.maximum(np.diff(session.nr.astype(float)), 1)
    counts[(session.nr[:-1] < 0) | (session.nr[1:] < 0)] = 1
    steps = np.diff(raw) / counts
    good = np.isfinite(steps) & (steps > 0) & (steps < 0.2)
    cadence = float(np.median(steps[good])) if good.any() else None
    hardware = next((k for k in ("T_IMU_US", "T_US", "IMU_T_US") if k in session.channels), None)
    if hardware:
        times = channel_values(session, hardware) * 1e-6
        positive = np.diff(times)
        interrupted[1:] = np.isfinite(positive) & (positive < 0)
        valid = np.isfinite(positive) & (positive > 0)
        if cadence is None and valid.any():
            cadence = float(np.median(positive[valid] / counts[valid]))
        dt = np.r_[0.0, positive]
        quality = np.zeros(n, dtype=np.int64)
        if not np.isfinite(times[0]):
            quality[0] = 2
        broken = ~np.isfinite(dt) | (dt < 0) | ((dt == 0) & (np.arange(n) != 0))
        quality[broken] = 2
        if broken.any() and cadence is None:
            raise ValueError("No usable IMU time interval; record a monotonic T_IMU_US clock")
        dt[broken] = (np.r_[1.0, counts] * (cadence or 0))[broken]
        warnings = (
            [f"Repaired {int(broken.sum())} invalid hardware clock intervals"]
            if broken.any()
            else []
        )
    else:
        if cadence is None:
            raise ValueError(
                "Cannot infer IMU timing. Record T_IMU_US; the display's 1 µs corrections "
                "are not integration time"
            )
        dt = np.r_[0.0, cadence * counts]
        quality = np.full(n, 2, dtype=np.int64)
        if np.isfinite(raw[0]):
            quality[0] = 1
        recorded = np.flatnonzero(np.isfinite(raw))
        for left, right in zip(recorded[:-1], recorded[1:], strict=True):
            interval = raw[right] - raw[left]
            count = float(np.sum(counts[left:right]))
            if 0.25 * cadence < interval / count < 4 * cadence:
                dt[left + 1 : right + 1] = interval * counts[left:right] / count
                if right == left + 1:
                    quality[right] = 1
            elif interval >= 4 * cadence * count:
                # A positive clock discontinuity can be a real recording interruption.
                # Preserve it as one gap so propagation stops instead of hiding missing IMU.
                dt[right] = max(interval - float(np.sum(dt[left + 1 : right])), cadence)
        warnings = [
            (
                "No monotonic IMU clock: integration uses logger times and explicitly "
                "estimated missing/backward intervals"
            )
        ]
    if not allow_estimated and (quality == 2).any():
        raise ValueError(
            "This log needs estimated IMU intervals. Enable legacy timing or record a "
            "valid T_IMU_US clock"
        )
    return NavigationClock(np.cumsum(dt), quality, warnings, interrupted)


@dataclass
class Observations:
    position: np.ndarray
    speed: np.ndarray
    altitude: np.ndarray
    position_new: np.ndarray
    speed_new: np.ndarray
    altitude_new: np.ndarray
    available: np.ndarray
    lat0: float
    lon0: float
    height0: float | None
    east_scale: float
    north_scale: float
    warnings: list[str]

    def geographic(self, position):
        lat = self.lat0 + np.degrees(position[..., 1] / self.north_scale)
        lon = self.lon0 + np.degrees(position[..., 0] / self.east_scale)
        return lat, (lon + 180) % 360 - 180


def gps_observations(session, time):
    lat, lon = (channel_values(session, k) for k in ("GPS_LAT", "GPS_LON"))
    speed = channel_values(session, "GPS_SPEED_KMH") / 3.6
    altitude = channel_values(session, "GPS_ALT_MSL_M")
    n = len(time)
    available = np.isfinite(lat) & np.isfinite(lon) & (abs(lat) <= 90) & (abs(lon) <= 180)
    state = session.logger_fields.get("GPS_STATE", [""] * n)
    gps_valid = channel_values(session, "GPS_VALID")
    for i, text in enumerate(state):
        if text and text.upper() not in ("N", "R"):
            available[i] = False
    available &= ~(np.isfinite(gps_valid) & (gps_valid == 0))
    fresh = channel_values(session, "GPS_NEW")
    epoch_name = next((k for k in ("GPS_FIX_ID", "GPS_ITOW_MS") if k in session.channels), None)
    epoch = channel_values(session, epoch_name) if epoch_name else np.full(n, np.nan)
    position_new = np.zeros(n, dtype=bool)
    speed_new = np.zeros(n, dtype=bool)
    altitude_new = np.zeros(n, dtype=bool)
    previous_position = previous_speed = previous_alt = previous_epoch = None
    anchor = None
    pending = None
    inferred = False
    rejected = 0
    for i in range(n):
        if not available[i]:
            previous_position = previous_speed = previous_alt = previous_epoch = None
            anchor = None
            pending = None
            continue
        current = (lat[i], lon[i])
        flag = state[i].upper()
        explicit = True
        if flag:
            new = flag == "N"
        elif np.isfinite(fresh[i]):
            new = fresh[i] > 0
        elif np.isfinite(epoch[i]):
            new = previous_epoch is None or epoch[i] != previous_epoch
        else:
            explicit = False
            inferred = True
            new = current != previous_position
        if epoch_name and np.isfinite(epoch[i]) and epoch[i] == previous_epoch:
            new = False
        if new and anchor is not None:
            distance = haversine_m(lat[anchor], lon[anchor], lat[i], lon[i])
            if distance > 100 * max(time[i] - time[anchor], 1e-6):
                recovered = pending is not None and haversine_m(
                    lat[pending], lon[pending], lat[i], lon[i]
                ) <= 100 * max(time[i] - time[pending], 1e-6)
                if not recovered:
                    rejected += 1
                    new = False
                    pending = i
        if new:
            position_new[i] = True
            anchor = i
            pending = None
        if explicit:
            speed_new[i] = new and np.isfinite(speed[i]) and speed[i] >= 0
            altitude_new[i] = new and np.isfinite(altitude[i])
        else:
            # An altitude-only update never repeats the previous horizontal position update.
            speed_new[i] = np.isfinite(speed[i]) and speed[i] >= 0 and speed[i] != previous_speed
            altitude_new[i] = np.isfinite(altitude[i]) and altitude[i] != previous_alt
        previous_position, previous_speed, previous_alt = current, speed[i], altitude[i]
        if np.isfinite(epoch[i]):
            previous_epoch = epoch[i]
    rows = np.flatnonzero(position_new)
    if not len(rows):
        raise ValueError("No new valid GPS positions; estimation starts only after a real GPS fix")
    lat0, lon0 = float(lat[rows[0]]), float(lon[rows[0]])
    if abs(lat0) > 85:
        raise ValueError(
            "Local vehicle navigation currently supports latitudes between 85°S and 85°N"
        )
    # WGS84 local metric coordinates; MSL is separate from ECEF ellipsoid height.
    phi = np.radians(lat0)
    e2, a = 6.69437999014e-3, 6378137.0
    east_scale = a / np.sqrt(1 - e2 * np.sin(phi) ** 2) * np.cos(phi)
    north_scale = a * (1 - e2) / (1 - e2 * np.sin(phi) ** 2) ** 1.5
    heights = np.flatnonzero(altitude_new & (np.arange(n) >= rows[0]))
    height0 = float(altitude[heights[0]]) if len(heights) else None
    position = np.column_stack(
        (
            np.radians((lon - lon0 + 180) % 360 - 180) * east_scale,
            np.radians(lat - lat0) * north_scale,
            altitude - height0 if height0 is not None else np.full(n, np.nan),
        )
    )
    warnings = []
    if inferred:
        warnings.append(
            "GPS freshness inferred from field changes; identical new fixes cannot be "
            "distinguished from held values"
        )
    if rejected:
        warnings.append(f"Rejected {rejected} GPS candidates exceeding 100 m/s")
    if np.nanmax(np.linalg.norm(position[rows, :2], axis=1)) > EARTH_RADIUS_M * 0.01:
        warnings.append(
            "Track exceeds 60 km from the local origin; local tangent approximation is degraded"
        )
    return Observations(
        position,
        speed,
        altitude,
        position_new,
        speed_new,
        altitude_new,
        available,
        lat0,
        lon0,
        height0,
        east_scale,
        north_scale,
        warnings,
    )
