"""Independent synthetic vehicle physics: rear/right/up axes, slope, turns and GNSS loss."""

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np


def trajectory(
    duration=80.0,
    step=0.02,
    seed=23,
    gps_noise=0.15,
    turns=True,
    variable_speed=True,
    slope_amplitude=0.03,
    slope_period_s=13.0,
):
    rng = np.random.default_rng(seed)
    time = np.arange(0, duration + step / 2, step)
    speed = 12 + 3 * np.sin(time / 6)
    acceleration = 0.5 * np.cos(time / 6)
    if not variable_speed:
        speed[:] = 12
        acceleration[:] = 0
    yaw = 0.3 * np.sin(time / 9) + time / 60
    yaw_rate = np.cos(time / 9) / 30 + 1 / 60
    if not turns:
        yaw[:] = 0
        yaw_rate[:] = 0
    pitch = slope_amplitude * np.sin(time / slope_period_s)
    pitch_rate = slope_amplitude / slope_period_s * np.cos(time / slope_period_s)
    direction = np.column_stack(
        (np.cos(pitch) * np.cos(yaw), np.cos(pitch) * np.sin(yaw), np.sin(pitch))
    )
    derivative = np.column_stack(
        (
            -pitch_rate * np.sin(pitch) * np.cos(yaw) - yaw_rate * np.cos(pitch) * np.sin(yaw),
            -pitch_rate * np.sin(pitch) * np.sin(yaw) + yaw_rate * np.cos(pitch) * np.cos(yaw),
            pitch_rate * np.cos(pitch),
        )
    )
    velocity = speed[:, None] * direction
    position = np.vstack(
        (np.zeros(3), np.cumsum((velocity[1:] + velocity[:-1]) * step / 2, axis=0))
    )
    world_acceleration = acceleration[:, None] * direction + speed[:, None] * derivative
    rotations = np.empty((len(time), 3, 3))
    rotations[:, :, 0] = -direction
    rotations[:, :, 1] = np.column_stack((np.sin(yaw), -np.cos(yaw), np.zeros(len(time))))
    rotations[:, :, 2] = np.column_stack(
        (-np.sin(pitch) * np.cos(yaw), -np.sin(pitch) * np.sin(yaw), np.cos(pitch))
    )
    force_body = np.einsum("nji,nj->ni", rotations, world_acceleration + [0, 0, 9.80665])
    angular_body = np.column_stack(
        (-yaw_rate * np.sin(pitch), pitch_rate, yaw_rate * np.cos(pitch))
    )
    # Explicit Euler products, independent of the application rotation routines.
    a, b, c = 0.6, -0.12, 0.08
    rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    rx = np.array([[1, 0, 0], [0, np.cos(c), -np.sin(c)], [0, np.sin(c), np.cos(c)]])
    mounting = rz @ ry @ rx
    ba = np.array([0.03, -0.02, 0.04])
    bg = np.radians([3.8, -8.7, -3.1])
    force = force_body @ mounting + ba + rng.normal(0, 0.01, force_body.shape)
    gyro = angular_body @ mounting + bg + rng.normal(0, 0.0003, angular_body.shape)
    measured_position = position + rng.normal(0, gps_noise, position.shape)
    return dict(
        time=time,
        force=force,
        gyro=gyro,
        mounting=mounting,
        ba=ba,
        bg=bg,
        position=position,
        measured_position=measured_position,
        velocity=velocity,
        acceleration=world_acceleration,
        force_body=force_body,
        rotations=rotations,
    )


def write_demo(path, data=None):
    data = trajectory() if data is None else data
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ["NR", "DATA", "CZAS", "T_IMU_US", "GPS_STATE", "GPS_FIX_ID"]
    header += [prefix + axis for prefix in ("ACC_", "GYRO_") for axis in "XYZ"]
    header += ["GPS_LAT", "GPS_LON", "GPS_SPEED_KMH", "GPS_ALT_MSL_M"]
    origin = datetime(2026, 1, 1, 12)
    last = None
    epoch = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(header)
        for i, t in enumerate(data["time"]):
            outage = t < 2 or 28 <= t < 35
            stamp = origin + timedelta(seconds=float(t))
            if outage:
                state, last = "I", None
            elif i % 10 == 0:
                epoch += 1
                p = data["measured_position"][i]
                last = [
                    np.degrees(p[1] / 6335439.3272928),
                    np.degrees(p[0] / 6378137),
                    np.linalg.norm(data["velocity"][i, :2]) * 3.6,
                    p[2] + 100,
                ]
                state = "N"
            else:
                state = "R"
            writer.writerow(
                [
                    70000 + i,
                    stamp.strftime("%Y-%m-%d") if not outage else "",
                    stamp.strftime("%H:%M:%S.%f") if not outage else "",
                    int(round(t * 1e6)) + 1000000,
                    state,
                    epoch,
                    *(data["force"][i] / 9.80665),
                    *np.degrees(data["gyro"][i]),
                    *(last if last is not None else [""] * 4),
                ]
            )
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    write_demo(args.path)
