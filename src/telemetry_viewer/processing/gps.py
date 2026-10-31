import numpy as np

from telemetry_viewer.domain.models import QualityIssue

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1, lon1, lat2, lon2):
    a1, a2 = np.radians(lat1), np.radians(lat2)
    da = a2 - a1
    db = np.radians(np.asarray(lon2) - lon1)
    a = np.sin(da / 2) ** 2 + np.cos(a1) * np.cos(a2) * np.sin(db / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def gps_distances(lat, lon, speed, time, lines, max_speed_m_s=100.0):
    """Extract observable GPS changes, rejecting implausible jumps and breaking gaps."""
    issues, fixes, starts = [], [], []
    distance = np.zeros(len(time), dtype=np.float64)
    previous_state = None
    anchor = None
    gap = True
    total = 0.0
    for i in range(len(time)):
        if not np.isfinite([lat[i], lon[i]]).all() or abs(lat[i]) > 90 or abs(lon[i]) > 180:
            if np.isfinite([lat[i], lon[i]]).any():
                issues.append(QualityIssue(lines[i], "invalid_gps", "Invalid WGS84 coordinates"))
            gap = True
            previous_state = None
            distance[i] = total
            continue
        state = (lat[i], lon[i], speed[i] if np.isfinite(speed[i]) else None)
        if state != previous_state:
            previous_state = state
            step = (
                0.0
                if anchor is None
                else float(haversine_m(lat[anchor], lon[anchor], lat[i], lon[i]))
            )
            dt = time[i] - time[anchor] if anchor is not None else 0
            if not gap and step > max_speed_m_s * max(dt, 1e-6):
                issues.append(
                    QualityIssue(lines[i], "gps_jump", "GPS jump exceeds 100 m/s; rejected")
                )
                distance[i] = total
                continue
            if not gap:
                total += step
            fixes.append(i)
            starts.append(gap)
            anchor, gap = i, False
        distance[i] = total
    return np.array(fixes, dtype=np.int64), np.array(starts, dtype=bool), distance, issues


def integrate_speed(speed_kmh, time_s):
    """Left rectangle (zero-order hold); invalid/negative speeds contribute zero."""
    speed = np.where(np.isfinite(speed_kmh) & (speed_kmh >= 0), speed_kmh / 3.6, 0)
    return np.r_[0.0, np.cumsum(speed[:-1] * np.maximum(np.diff(time_s), 0))]
