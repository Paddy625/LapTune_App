"""Moving calibration and 15-error-state inertial/GNSS fusion; original channels are read-only."""

from dataclasses import asdict, dataclass

import numpy as np

from telemetry_viewer.domain.models import Channel
from telemetry_viewer.processing.load_filter import smooth_loads
from telemetry_viewer.processing.navigation_calibration import GRAVITY, calibrate_driving
from telemetry_viewer.processing.navigation_inputs import (
    channel_values,
    gps_observations,
    navigation_clock,
)
from telemetry_viewer.processing.rotations import (
    VEHICLE_AXES,
    VEHICLE_FRAME,
    attitude_angles,
    rotation_exp,
    skew,
    track_rotation,
)


@dataclass(frozen=True)
class NavigationConfig:
    acceleration_unit: str = "g"
    gyro_unit: str = "deg/s"
    calibration_rows: tuple[int, int] | None = None
    allow_estimated_time: bool = True
    reference_window_s: float = 2.5  # Half-width of offline calibration fits.
    reference_gap_s: float = 2.5
    minimum_speed_m_s: float = 3.0
    gps_position_sigma_m: float = 5.0
    gps_altitude_sigma_m: float = 10.0
    gps_speed_sigma_m_s: float = 0.5
    acceleration_noise: float = 0.35
    gyro_noise: float = 0.012
    acceleration_bias_walk: float = 0.005
    gyro_bias_walk: float = 0.0005
    maximum_imu_gap_s: float = 0.25
    unreliable_radius_m: float = 50.0
    vehicle_constraint: bool = False
    assume_zero_mean_bank: bool = True
    load_cutoff_hz: float = 5.0

    def validate(self):
        if self.acceleration_unit not in ("g", "m/s²") or self.gyro_unit not in ("deg/s", "rad/s"):
            raise ValueError("Confirm physical ACC and GYRO units before estimating motion")
        for name, value in asdict(self).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not np.isfinite(value) or value <= 0:
                    raise ValueError(f"{name} must be finite and positive")
        if not self.assume_zero_mean_bank:
            raise ValueError(
                "One GPS antenna cannot distinguish mounting roll from unknown mean vehicle"
                " bank. Select a segment with negligible mean bank"
            )


@dataclass
class NavigationResult:
    channels: dict[str, Channel]
    report: dict
    segment_start: np.ndarray


DESCRIPTIONS = {
    "CALC_LAT": ("deg", "Estimated vehicle latitude from IMU/GPS ESKF"),
    "CALC_LON": ("deg", "Estimated vehicle longitude from IMU/GPS ESKF"),
    "CALC_ALT_MSL_M": (
        "m",
        "Estimated height above mean sea level; available only with a measured MSL reference",
    ),
    "CALC_SPEED_KMH": ("km/h", "Estimated horizontal ground speed"),
    "CALC_VEL_X": ("m/s", "Estimated body velocity along forward X; forward travel is positive"),
    "CALC_VEL_Y": ("m/s", "Estimated body velocity towards the left"),
    "CALC_VEL_Z": ("m/s", "Estimated body velocity upwards"),
    "CALC_ACC_BIAS_X": ("m/s²", "Estimated ACC bias to subtract along forward vehicle X"),
    "CALC_ACC_BIAS_Y": ("m/s²", "Estimated ACC bias to subtract along leftward vehicle Y"),
    "CALC_ACC_BIAS_Z": ("m/s²", "Estimated ACC bias to subtract along upward vehicle Z"),
    "CALC_GYRO_BIAS_X": ("deg/s", "Estimated GYRO bias to subtract about forward vehicle X"),
    "CALC_GYRO_BIAS_Y": ("deg/s", "Estimated GYRO bias to subtract about leftward vehicle Y"),
    "CALC_GYRO_BIAS_Z": ("deg/s", "Estimated GYRO bias to subtract about upward vehicle Z"),
    "CALC_DISTANCE_M": (
        "m",
        (
            "Estimated horizontal distance integrated from fused speed; resets "
            "integration across invalid IMU gaps"
        ),
    ),
    "CALC_ACC_X": (
        "m/s²",
        "Estimated linear acceleration X, positive forward; gravity and estimated bias removed",
    ),
    "CALC_ACC_Y": (
        "m/s²",
        "Estimated linear acceleration Y, positive left; gravity and estimated bias removed",
    ),
    "CALC_ACC_Z": (
        "m/s²",
        "Estimated linear acceleration Z, positive up; gravity and estimated bias removed",
    ),
    "CALC_G_LOAD_X": (
        "g",
        "Motion-induced inertial load X = -CALC_ACC_X / 9.80665; positive forward "
        "when braking, negative when accelerating forward; gravity excluded",
    ),
    "CALC_G_LOAD_Y": (
        "g",
        "Motion-induced inertial load Y = -CALC_ACC_Y / 9.80665; positive left "
        "in a right turn, negative in a left turn; gravity excluded",
    ),
    "CALC_G_LOAD_Z": (
        "g",
        "Motion-induced inertial load Z = -CALC_ACC_Z / 9.80665; positive up "
        "during downward acceleration; gravity excluded, zero at rest (not static 1 g)",
    ),
    **{
        "CALC_G_LOAD_LP_" + axis: (
            "g",
            f"Smoothed motion-induced inertial load {axis}; Butterworth zero phase; "
            "gravity excluded; unavailable near run boundaries",
        )
        for axis in "XYZ"
    },
    "CALC_GYRO_X": (
        "deg/s",
        "Estimated angular rate about forward X; mounting and bias compensated",
    ),
    "CALC_GYRO_Y": (
        "deg/s",
        "Estimated angular rate about leftward Y; positive front down; "
        "mounting and bias compensated",
    ),
    "CALC_GYRO_Z": (
        "deg/s",
        "Estimated angular rate about upward Z; positive left turn; mounting and bias compensated",
    ),
    "CALC_ROLL_DEG": ("deg", "Estimated right-hand roll about forward X"),
    "CALC_PITCH_DEG": (
        "deg",
        "Estimated pitch angle, positive front up; opposite right-hand rotation about leftward Y",
    ),
    "CALC_HEADING_DEG": (
        "deg",
        "Estimated vehicle forward heading along +X, clockwise from true north",
    ),
    "CALC_POS_SIGMA_M": (
        "m",
        "Model horizontal RMS position uncertainty, sqrt(P_EE + P_NN); not measured accuracy",
    ),
    "CALC_POS_95_M": (
        "m",
        (
            "Conservative model 95% horizontal uncertainty radius; assumes calibrated "
            "Gaussian covariance"
        ),
    ),
    "CALC_GPS_AGE_S": ("s", "Integration time since the last accepted new horizontal GPS position"),
    "CALC_NAV_STATUS": (
        "code",
        (
            "0 waiting for initialization; 1 GPS aided; 2 dead reckoning; 3 low "
            "confidence; 4 IMU interruption, waiting for reinitialization"
        ),
    ),
    "CALC_TIME_S": (
        "s",
        "Separate navigation integration clock; original display times are preserved",
    ),
    "CALC_TIME_QUALITY": (
        "code",
        "0 hardware monotonic clock; 1 logger date/time interval; 2 estimated/repaired interval",
    ),
}


class InertialFilter:
    """Right multiplicative attitude errors; biases expressed in the fixed vehicle frame."""

    def __init__(self, position, velocity, rotation, ba, bg, config):
        self.p, self.v, self.rotation = position.copy(), velocity.copy(), rotation.copy()
        self.ba, self.bg = ba.copy(), bg.copy()
        self.config = config
        self.covariance = np.diag(
            [config.gps_position_sigma_m**2] * 2
            + [config.gps_altitude_sigma_m**2]
            + [2.0**2] * 3
            + [np.radians(4) ** 2] * 3
            + [0.15**2] * 3
            + [np.radians(0.5) ** 2] * 3
        )

    def predict(self, force, gyro, dt, degraded=False):
        acceleration, angular = force - self.ba, gyro - self.bg
        middle = self.rotation @ rotation_exp(angular * dt / 2)
        world_acceleration = middle @ acceleration + [0, 0, -GRAVITY]
        self.p += self.v * dt + world_acceleration * (dt * dt / 2)
        self.v += world_acceleration * dt
        self.rotation = self.rotation @ rotation_exp(angular * dt)
        transition = np.zeros((15, 15))
        transition[0:3, 3:6] = np.eye(3)
        transition[3:6, 6:9] = -middle @ skew(acceleration)
        transition[3:6, 9:12] = -middle
        transition[6:9, 6:9] = -skew(angular)
        transition[6:9, 12:15] = -np.eye(3)
        phi = np.eye(15) + transition * dt + (transition @ transition) * (dt * dt / 2)
        q = np.zeros((15, 15))
        scale = 9.0 if degraded else 1.0
        accel_var = self.config.acceleration_noise**2 * scale
        q[0:3, 0:3] = np.eye(3) * accel_var * dt**3 / 3
        q[0:3, 3:6] = q[3:6, 0:3] = np.eye(3) * accel_var * dt**2 / 2
        q[3:6, 3:6] = np.eye(3) * accel_var * dt
        q[6:9, 6:9] = np.eye(3) * self.config.gyro_noise**2 * dt * scale
        q[9:12, 9:12] = np.eye(3) * self.config.acceleration_bias_walk**2 * dt
        q[12:15, 12:15] = np.eye(3) * self.config.gyro_bias_walk**2 * dt
        self.covariance = phi @ self.covariance @ phi.T + q

    def update(self, innovation, jacobian, measurement_covariance, gate=13.82):
        p = self.covariance
        cross = p @ jacobian.T
        s = jacobian @ cross + measurement_covariance
        solved = np.linalg.solve(s, innovation)
        if float(innovation @ solved) > gate:
            return False
        gain = np.linalg.solve(s, cross.T).T
        correction = gain @ innovation
        self.p += correction[0:3]
        self.v += correction[3:6]
        self.rotation = self.rotation @ rotation_exp(correction[6:9])
        self.ba += correction[9:12]
        self.bg += correction[12:15]
        remaining = np.eye(15) - gain @ jacobian
        p = remaining @ p @ remaining.T + gain @ measurement_covariance @ gain.T
        reset = np.eye(15)
        reset[6:9, 6:9] -= 0.5 * skew(correction[6:9])
        self.covariance = reset @ p @ reset.T
        self.covariance = (self.covariance + self.covariance.T) / 2
        return True


def estimate_navigation(session, config=None, progress=lambda percent: None):
    config = NavigationConfig() if config is None else config
    config.validate()
    progress(0)
    required = [prefix + axis for prefix in ("ACC_", "GYRO_") for axis in "XYZ"]
    for name in required + ["GPS_LAT", "GPS_LON"]:
        if name not in session.channels or session.channels[name].source != "Logger":
            raise ValueError(f"Navigation requires the original logger channel {name}")
    collisions = [
        n
        for n in DESCRIPTIONS
        if n in session.channels
        and (
            session.navigation is None
            or n not in session.navigation.channels
            or session.channels[n] is not session.navigation.channels[n]
        )
    ]
    if collisions:
        raise ValueError(
            (
                "CALC output names already exist as other channels; original channels will "
                "not be replaced: "
            )
            + ", ".join(collisions)
        )
    clock = navigation_clock(session, config.allow_estimated_time)
    time, n = clock.time, len(clock.time)
    force = np.column_stack([channel_values(session, "ACC_" + axis) for axis in "XYZ"])
    gyro = np.column_stack([channel_values(session, "GYRO_" + axis) for axis in "XYZ"])
    force *= GRAVITY if config.acceleration_unit == "g" else 1.0
    gyro *= np.pi / 180 if config.gyro_unit == "deg/s" else 1.0
    observations = gps_observations(session, time)
    progress(15)
    calibration = calibrate_driving(time, force, gyro, observations, config, progress)
    mounting = np.array(calibration.rotation_sensor_to_vehicle)
    ba = mounting @ calibration.acceleration_bias_sensor
    bg = mounting @ calibration.gyro_bias_sensor
    force, gyro = force @ mounting.T, gyro @ mounting.T
    imu_valid = np.isfinite(force).all(axis=1) & np.isfinite(gyro).all(axis=1)
    data = {name: np.full(n, np.nan) for name in DESCRIPTIONS}
    data["CALC_TIME_S"] = time.copy()
    data["CALC_TIME_QUALITY"] = clock.quality.astype(float)
    data["CALC_NAV_STATUS"][:] = 0
    starts = np.zeros(n, dtype=bool)
    positions = np.full((n, 3), np.nan)
    accepted, rejected, reinitializations = 0, 0, 0
    estimator = None
    history = []
    last_accepted = None
    distance = 0.0
    previous_speed = None
    position_rows = np.flatnonzero(observations.position_new)
    intervals = np.diff(time[position_rows])
    nominal = (
        np.median(intervals[intervals < config.reference_gap_s])
        if (intervals < config.reference_gap_s).any()
        else 1.0
    )
    outage_age = max(0.6, 3 * float(nominal))
    consecutive_rejected = 0
    gap_wait = False
    for i in range(n):
        if i % 128 == 0:
            progress(40 + int(59 * i / max(n, 1)))
        dt = time[i] - time[i - 1] if i else 0.0
        if (
            not imu_valid[i]
            or clock.interrupted[i]
            or (i and (dt <= 0 or dt > config.maximum_imu_gap_s))
        ):
            estimator = None
            history.clear()
            previous_speed = None
            gap_wait = True
            data["CALC_NAV_STATUS"][i] = 4
            continue
        new_position = observations.position_new[i]
        if new_position:
            history.append(i)
            history = [row for row in history if time[i] - time[row] <= max(3.0, 3 * nominal)]
        if estimator is None:
            data["CALC_NAV_STATUS"][i] = 4 if gap_wait else 0
            if not new_position or len(history) < 2:
                continue
            first = history[0]
            displacement = observations.position[i, :2] - observations.position[first, :2]
            span = time[i] - time[first]
            if span <= 0 or np.linalg.norm(displacement) < max(5, 2 * config.gps_position_sigma_m):
                continue
            velocity = np.r_[displacement / span, 0.0]
            horizontal = np.linalg.norm(velocity[:2])
            native = observations.speed[i]
            if np.isfinite(native) and native >= 0:
                velocity[:2] *= native / max(horizontal, 1e-9)
            if np.linalg.norm(velocity[:2]) < config.minimum_speed_m_s:
                continue
            if np.isfinite(observations.position[[first, i], 2]).all():
                velocity[2] = np.clip(
                    (observations.position[i, 2] - observations.position[first, 2]) / span,
                    -0.2 * np.linalg.norm(velocity[:2]),
                    0.2 * np.linalg.norm(velocity[:2]),
                )
            initial = observations.position[i].copy()
            initial[2] = initial[2] if np.isfinite(initial[2]) else 0.0
            estimator = InertialFilter(initial, velocity, track_rotation(velocity), ba, bg, config)
            starts[i] = True
            reinitializations += 1
            last_accepted = time[i]
            accepted += 1
            gap_wait = False
        else:
            # Never bridge a missing IMU row, even if the current row has valid samples.
            if i and not imu_valid[i - 1]:
                estimator = None
                previous_speed = None
                data["CALC_NAV_STATUS"][i] = 4
                continue
            f = (force[i - 1] + force[i]) / 2 if i else force[i]
            w = (gyro[i - 1] + gyro[i]) / 2 if i else gyro[i]
            estimator.predict(f, w, dt, clock.quality[i] == 2)
            if new_position:
                h = np.zeros((2, 15))
                h[:, :2] = np.eye(2)
                sigma = config.gps_position_sigma_m
                accepted_position = estimator.update(
                    observations.position[i, :2] - estimator.p[:2], h, np.eye(2) * sigma**2
                )
                if accepted_position:
                    accepted += 1
                    last_accepted = time[i]
                    consecutive_rejected = 0
                else:
                    rejected += 1
                    consecutive_rejected += 1
                    # A long outage can invalidate the inertial state. Reinitialize explicitly
                    # from several consistent fixes after recovery.
                    if consecutive_rejected >= 3 and time[i] - last_accepted > 10:
                        estimator = None
                        previous_speed = None
                        gap_wait = True
                        data["CALC_NAV_STATUS"][i] = 4
                        continue
            else:
                accepted_position = True
            if observations.speed_new[i] and accepted_position:
                speed = np.linalg.norm(estimator.v[:2])
                if speed > 0.2:
                    h = np.zeros((1, 15))
                    h[0, 3:5] = estimator.v[:2] / speed
                    estimator.update(
                        np.array([observations.speed[i] - speed]),
                        h,
                        np.array([[config.gps_speed_sigma_m_s**2]]),
                        gate=10.83,
                    )
            if (
                observations.altitude_new[i]
                and accepted_position
                and observations.height0 is not None
            ):
                h = np.zeros((1, 15))
                h[0, 2] = 1
                estimator.update(
                    np.array([observations.altitude[i] - observations.height0 - estimator.p[2]]),
                    h,
                    np.array([[config.gps_altitude_sigma_m**2]]),
                    gate=10.83,
                )
            if (
                config.vehicle_constraint
                and i % 10 == 0
                and abs((gyro[i] - estimator.bg)[2]) < 0.15
            ):
                body_velocity = estimator.rotation.T @ estimator.v
                h = np.zeros((2, 15))
                h[:, 3:6] = estimator.rotation.T[1:3]
                h[:, 6:9] = skew(body_velocity)[1:3]
                estimator.update(-body_velocity[1:3], h, np.eye(2) * 1.5**2, gate=9.21)
        if not np.isfinite(estimator.p).all() or not np.isfinite(estimator.covariance).all():
            raise ValueError(
                "Navigation state diverged; check IMU units, timestamps and calibration"
            )
        positions[i] = estimator.p
        eigen = np.linalg.eigvalsh(estimator.covariance[:2, :2])
        radius = float(np.sqrt(5.991 * max(eigen[-1], 0)))
        speed = float(np.linalg.norm(estimator.v[:2]))
        if previous_speed is not None:
            distance += (previous_speed + speed) / 2 * dt
        previous_speed = speed
        age = time[i] - last_accepted
        mode = 2 if age > outage_age or not observations.available[i] else 1
        if radius > config.unreliable_radius_m or clock.quality[i] == 2:
            mode = 3
        data["CALC_NAV_STATUS"][i] = mode
        data["CALC_SPEED_KMH"][i] = speed * 3.6
        data["CALC_DISTANCE_M"][i] = distance
        data["CALC_POS_SIGMA_M"][i] = np.sqrt(max(float(np.trace(estimator.covariance[:2, :2])), 0))
        data["CALC_POS_95_M"][i] = radius
        data["CALC_GPS_AGE_S"][i] = age
        linear_body = force[i] - estimator.ba + estimator.rotation.T @ [0, 0, -GRAVITY]
        angular_body = np.degrees(gyro[i] - estimator.bg)
        body_velocity = estimator.rotation.T @ estimator.v
        for axis, value, angular in zip("XYZ", linear_body, angular_body, strict=True):
            data["CALC_ACC_" + axis][i] = value
            data["CALC_G_LOAD_" + axis][i] = -value / GRAVITY
            data["CALC_GYRO_" + axis][i] = angular
        for index, axis in enumerate("XYZ"):
            data["CALC_VEL_" + axis][i] = body_velocity[index]
            data["CALC_ACC_BIAS_" + axis][i] = estimator.ba[index]
            data["CALC_GYRO_BIAS_" + axis][i] = np.degrees(estimator.bg[index])
        for name, value in zip(
            ("CALC_ROLL_DEG", "CALC_PITCH_DEG", "CALC_HEADING_DEG"),
            attitude_angles(estimator.rotation),
            strict=True,
        ):
            data[name][i] = value
    valid_position = np.isfinite(positions).all(axis=1)
    if not valid_position.any():
        raise ValueError(
            "No GPS-initialized navigation solution. Need forward movement spanning "
            "more than GPS position uncertainty"
        )
    data["CALC_LAT"], data["CALC_LON"] = observations.geographic(positions)
    if observations.height0 is not None:
        data["CALC_ALT_MSL_M"] = positions[:, 2] + observations.height0
    smoothed, smoothing = smooth_loads(
        time,
        np.column_stack([data["CALC_G_LOAD_" + axis] for axis in "XYZ"]),
        starts,
        config.load_cutoff_hz,
        config.maximum_imu_gap_s,
        check=lambda: progress(99),
    )
    for j, axis in enumerate("XYZ"):
        data["CALC_G_LOAD_LP_" + axis] = smoothed[:, j]
    warnings = clock.warnings + observations.warnings
    if smoothing["short_runs_skipped"] or smoothing["undersampled_runs_skipped"]:
        warnings.append(
            "Smoothed load channels exclude short or undersampled runs; "
            "see load_smoothing metadata and select a lower cutoff for a slower IMU"
        )
    if calibration.acceleration_rms_m_s2 > 1 or calibration.gyro_rms_rad_s > 0.06:
        warnings.append(
            "Large driving-calibration residuals: mounting and biases are approximate. "
            "Try a cleaner calibration segment and verify timing, units and road assumptions."
        )
    warnings.append(
        "Calibration assumes forward travel, negligible sideslip and zero mean "
        "bank; mounting roll and unknown mean bank cannot be separated"
    )
    if observations.height0 is None:
        warnings.append(
            "No measured altitude: calibration uses an explicitly assumed level road; "
            "CALC_ALT_MSL_M is unavailable"
        )
    if (clock.quality == 2).any():
        warnings.append(
            "Estimated-time process noise is inflated heuristically; uncertainty is "
            "model-based, not a validated accuracy bound"
        )
    report = {
        "algorithm": "15-error-state multiplicative SO(3) IMU/GNSS filter v2 (FLU)",
        "axes": VEHICLE_AXES + "; world East, North, Up",
        "vehicle_frame": VEHICLE_FRAME,
        "output_prefix": "CALC_",
        "load_smoothing": smoothing,
        "g_load": {
            "formula": "CALC_G_LOAD_axis = -CALC_ACC_axis / gravity_m_s2",
            "gravity_m_s2": GRAVITY,
            "unit": "g",
            "includes_gravity": False,
            "description": "Motion-induced inertial load, not total apparent load; zero at rest",
        },
        "pitch_convention": "Positive front up, opposite right-hand rotation about leftward +Y",
        "config": asdict(config),
        "calibration": calibration.to_dict(),
        "warnings": warnings,
        "accepted_position_updates": accepted,
        "rejected_position_updates": rejected,
        "initializations": reinitializations,
        "estimated_time_rows": int(np.sum(clock.quality == 2)),
        "first_estimated_row": int(np.flatnonzero(valid_position)[0]),
        "origin": {
            "latitude": observations.lat0,
            "longitude": observations.lon0,
            "height_msl_m": observations.height0,
        },
        "assumptions": (
            "Co-located GNSS antenna and IMU; local WGS84 projection; no automatic "
            "zero-velocity updates; no RTS smoothing"
        ),
    }
    channels = {}
    for name, (unit, description) in DESCRIPTIONS.items():
        if name.startswith("CALC_G_LOAD_LP_"):
            description += (
                f"; from CALC_G_LOAD_{name[-1]}; final -3 dB cutoff "
                f"{config.load_cutoff_hz:g} Hz; edge guard {smoothing['edge_guard_s']:g} s"
            )
        samples = data[name]
        channels[name] = Channel(
            name,
            unit,
            "CALC: " + description,
            "Calculated",
            samples,
            session.elapsed_time_s,
            np.isfinite(samples),
            calculation={"kind": "navigation_eskf_v2", "report": report},
            description=description,
        )
    progress(100)
    return NavigationResult(channels, report, starts)
