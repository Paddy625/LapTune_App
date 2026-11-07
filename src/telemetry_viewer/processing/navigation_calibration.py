"""Moving-only mounting/bias calibration against a smoothed, zero-bank GNSS reference."""

from dataclasses import asdict, dataclass

import numpy as np

from telemetry_viewer.processing.rotations import VEHICLE_FRAME, rotation_log, track_rotation

GRAVITY = 9.80665


@dataclass
class Calibration:
    rotation_sensor_to_vehicle: list
    acceleration_bias_sensor: list
    gyro_bias_sensor: list
    acceleration_rms_m_s2: float
    gyro_rms_rad_s: float
    excitation_ratio: float
    samples: int
    start_time_s: float
    end_time_s: float
    reference: str = "Moving GNSS reference; forward travel; negligible sideslip and mean bank"

    def to_dict(self):
        result = asdict(self)
        result["units"] = {"acceleration_bias_sensor": "m/s²", "gyro_bias_sensor": "rad/s"}
        result["vehicle_frame"] = VEHICLE_FRAME
        return result


def local_polynomial(time, values, center, radius, degree=2):
    """Time-aware local fit; no differentiation of adjacent noisy GNSS points."""
    lo, hi = np.searchsorted(time, [center - radius, center + radius], side="left")
    if hi - lo < degree + 2:
        return None
    t, data = time[lo:hi] - center, values[lo:hi]
    if t[0] > -radius * 0.4 or t[-1] < radius * 0.4:
        return None
    weight = np.sqrt(np.maximum(0.05, (1 - np.minimum(abs(t) / radius, 1) ** 3) ** 3))
    design = np.column_stack([t**i for i in range(degree + 1)])
    coeff, _, rank, _ = np.linalg.lstsq(
        design * weight[:, None], data * weight[:, None], rcond=None
    )
    return coeff if rank == degree + 1 else None


def fit_mounting(force_sensor, gyro_sensor, force_reference, gyro_reference, check=lambda: None):
    """Centered, robust Wahba fit eliminates constant biases while identifying mounting."""
    arrays = [force_sensor, gyro_sensor, force_reference, gyro_reference]
    if any(not np.isfinite(a).all() for a in arrays):
        raise ValueError("Calibration contains invalid inputs")
    weights = np.ones(len(force_sensor))
    rotation = np.eye(3)
    for _ in range(8):
        check()
        means = [np.average(a, axis=0, weights=weights) for a in arrays]
        centered = [a - mean for a, mean in zip(arrays, means, strict=True)]
        # Physical scales prevent force units from overwhelming angular rates.
        source = np.vstack((centered[0] / 0.8, centered[1] / 0.04))
        target = np.vstack((centered[2] / 0.8, centered[3] / 0.04))
        repeated_weights = np.r_[weights, weights]
        left, singular, right = np.linalg.svd(source.T @ (target * repeated_weights[:, None]))
        sign = np.diag([1, 1, np.linalg.det(right.T @ left.T)])
        rotation = right.T @ sign @ left.T
        ba = means[0] - rotation.T @ means[2]
        bg = means[1] - rotation.T @ means[3]
        ra = (force_sensor - ba) @ rotation.T - force_reference
        rg = (gyro_sensor - bg) @ rotation.T - gyro_reference
        residual = np.sqrt(np.sum((ra / 0.8) ** 2 + (rg / 0.04) ** 2, axis=1))
        weights = np.minimum(1, 2.5 / np.maximum(residual, 1e-9))
    ratio = float(singular[1] / max(singular[0], 1e-12))
    spread = np.linalg.svd(target, compute_uv=False)
    if ratio < 0.025 or len(spread) < 2 or spread[1] / np.sqrt(len(target)) < 0.08:
        raise ValueError(
            "Driving calibration is not observable: choose forward driving with "
            "acceleration/braking and turns or changes of grade. Constant speed on "
            "a constant straight slope is insufficient."
        )
    rms_a = float(np.sqrt(np.average(np.sum(ra**2, axis=1), weights=weights)))
    rms_g = float(np.sqrt(np.average(np.sum(rg**2, axis=1), weights=weights)))
    if rms_a > 2.0 or rms_g > 0.15 or np.linalg.norm(ba) > 2 or np.linalg.norm(bg) > 0.4:
        raise ValueError(
            "Calibration does not agree with IMU/GPS motion. Check sensor units, clock alignment, "
            "GPS height quality, forward travel and a segment without significant bank/sideslip."
        )
    return rotation, ba, bg, rms_a, rms_g, ratio


def calibrate_driving(time, force, gyro, observations, config, progress=lambda x: None):
    rows = np.flatnonzero(observations.position_new)
    mask = np.ones(len(rows), dtype=bool)
    if config.calibration_rows is not None:
        lo, hi = config.calibration_rows
        mask &= (rows >= lo) & (rows <= hi)
    rows = rows[mask]
    radius = config.reference_window_s
    gaps = np.r_[True, np.diff(time[rows]) > config.reference_gap_s]
    groups = np.cumsum(gaps)
    reference_rows, reference_rotations, speeds = [], [], []
    for number, group in enumerate(np.unique(groups)):
        progress(15 + int(10 * number / max(len(np.unique(groups)), 1)))
        part = rows[groups == group]
        if len(part) < 9 or time[part[-1]] - time[part[0]] < 2 * radius:
            continue
        clock = time[part]
        native = observations.speed[part]
        heights = np.flatnonzero(
            observations.altitude_new & (time >= clock[0]) & (time <= clock[-1])
        )
        for index, row in enumerate(part):
            if index % 32 == 0:
                progress(25)
            fit = local_polynomial(clock, observations.position[part, :2], time[row], radius)
            if fit is None:
                continue
            vh = float(np.linalg.norm(fit[1]))
            if vh < config.minimum_speed_m_s:
                continue
            vertical_speed = 0.0
            if len(heights) >= 5:
                height_fit = local_polynomial(
                    time[heights], observations.altitude[heights, None], time[row], radius
                )
                if height_fit is not None:
                    vertical_speed = float(height_fit[1, 0])
            if abs(vertical_speed) > vh * 0.35:
                continue  # Implausible road grade / noisy altitude does not define orientation.
            native_good = np.isfinite(native) & (native >= 0)
            speed_fit = local_polynomial(
                clock[native_good], native[native_good, None], time[row], radius
            )
            horizontal_speed = max(float(speed_fit[0, 0]), 0) if speed_fit is not None else vh
            if horizontal_speed < config.minimum_speed_m_s:
                continue
            velocity = np.r_[fit[1] / vh * horizontal_speed, vertical_speed]
            reference_rows.append(row)
            reference_rotations.append(track_rotation(velocity))
            speeds.append(float(np.linalg.norm(velocity)))
    if len(reference_rows) < 12:
        raise ValueError(
            "Too few moving GPS samples for calibration. Select a longer continuous "
            "driving segment with valid GPS"
        )
    reference_rows = np.asarray(reference_rows)
    reference_rotations = np.asarray(reference_rotations)
    clock = time[reference_rows]
    speeds = np.asarray(speeds)
    force_reference, gyro_reference, force_sensor, gyro_sensor, used = [], [], [], [], []
    for j in range(1, len(reference_rows) - 1):
        if j % 64 == 0:
            progress(30)
        before, after = j - 1, j + 1
        if max(clock[j] - clock[before], clock[after] - clock[j]) > config.reference_gap_s:
            continue
        angular = rotation_log(reference_rotations[before].T @ reference_rotations[after]) / (
            clock[after] - clock[before]
        )
        # Express the centered rotation rate in the central body frame.
        angular = reference_rotations[j].T @ reference_rotations[before] @ angular
        acceleration = np.array(
            [
                (speeds[after] - speeds[before]) / (clock[after] - clock[before]),
                speeds[j] * angular[2],
                -speeds[j] * angular[1],
            ]
        )
        expected = acceleration - reference_rotations[j].T @ np.array([0, 0, -GRAVITY])
        lo, hi = np.searchsorted(time, [clock[j] - 0.08, clock[j] + 0.08])
        good = np.isfinite(force[lo:hi]).all(axis=1) & np.isfinite(gyro[lo:hi]).all(axis=1)
        if not good.any():
            continue
        force_sensor.append(np.mean(force[lo:hi][good], axis=0))
        gyro_sensor.append(np.mean(gyro[lo:hi][good], axis=0))
        force_reference.append(expected)
        gyro_reference.append(angular)
        used.append(reference_rows[j])
    if len(used) < 12:
        raise ValueError("Too few synchronized IMU/GPS samples for driving calibration")
    result = fit_mounting(
        *map(np.asarray, [force_sensor, gyro_sensor, force_reference, gyro_reference]),
        check=lambda: progress(35),
    )
    rotation, ba, bg, rms_a, rms_g, ratio = result
    return Calibration(
        rotation.tolist(),
        ba.tolist(),
        bg.tolist(),
        rms_a,
        rms_g,
        ratio,
        len(used),
        float(time[used[0]]),
        float(time[used[-1]]),
    )
