import hashlib
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from generate_navigation_demo import trajectory, write_demo

from telemetry_viewer.exporters.files import export_csv, export_estimated_gpx, export_gpx
from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter
from telemetry_viewer.processing.navigation import (
    InertialFilter,
    NavigationConfig,
    estimate_navigation,
)
from telemetry_viewer.processing.navigation_calibration import fit_mounting
from telemetry_viewer.processing.navigation_inputs import gps_observations, navigation_clock
from telemetry_viewer.processing.rotations import (
    attitude_angles,
    rotation_exp,
    rotation_log,
    track_rotation,
)


@pytest.fixture(scope="module")
def navigation_data(tmp_path_factory):
    truth = trajectory()
    path = write_demo(tmp_path_factory.mktemp("navigation") / "synthetic.TXT", truth)
    session = LegacyTxtImporter().load(path)
    return session, truth


def test_rotations_forward_left_up_and_limits():
    rotation = track_rotation(np.array([10.0, 0, 0]))
    np.testing.assert_allclose(rotation.T @ [10, 0, 0], [10, 0, 0])
    np.testing.assert_allclose(rotation, np.eye(3))
    np.testing.assert_allclose(np.cross(rotation[:, 0], rotation[:, 1]), rotation[:, 2])
    for vector in ([0, 0, 0], [0.1, -0.2, 0.3], [np.pi, 0, 0]):
        np.testing.assert_allclose(
            rotation_exp(rotation_log(rotation_exp(vector))), rotation_exp(vector), atol=1e-9
        )


def test_hardware_clock_and_freshness(navigation_data):
    session, truth = navigation_data
    clock = navigation_clock(session, allow_estimated=False)
    np.testing.assert_allclose(clock.time, truth["time"], atol=1e-10)
    assert not clock.quality.any()
    obs = gps_observations(session, clock.time)
    assert not obs.position_new[:100].any()
    assert obs.position_new[100] and not obs.position_new[101]
    assert not obs.position_new[1400:1750].any()
    assert obs.position_new[1750]
    assert not obs.warnings


def test_legacy_clock_missing_and_backward_intervals_do_not_use_display_corrections(log_file):
    session = LegacyTxtImporter().load(log_file)
    raw = list(session.timestamp_raw)
    for i in range(140, len(raw)):
        raw[i] = (
            datetime.strptime(raw[i], "%Y-%m-%d %H:%M:%S.%f") - timedelta(seconds=3)
        ).strftime("%Y-%m-%d %H:%M:%S.%f")
    for i in [0, 1, 50, 51, 52, 300]:
        raw[i] = " "
    session = replace(session, timestamp_raw=raw)
    clock = navigation_clock(session)
    np.testing.assert_allclose(clock.time, np.arange(301) / 100, atol=2e-7)
    assert clock.quality[140] == 2 and clock.quality[51] == 2
    assert session.timestamp_raw == raw
    with pytest.raises(ValueError, match="estimated IMU"):
        navigation_clock(session, allow_estimated=False)


def test_real_recording_gap_is_not_hidden_by_navigation_clock(log_file):
    session = LegacyTxtImporter().load(log_file)
    raw = [
        (
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
            + timedelta(seconds=5 if i >= 150 else 0)
        ).strftime("%Y-%m-%d %H:%M:%S.%f")
        for i, value in enumerate(session.timestamp_raw)
    ]
    clock = navigation_clock(replace(session, timestamp_raw=raw))
    assert clock.time[150] - clock.time[149] == pytest.approx(5.01, abs=1e-7)


def test_identical_new_gps_fix_is_assimilated_once_and_repeats_are_ignored(log_file):
    session = LegacyTxtImporter().load(log_file)
    flags = ["R"] * len(session.nr)
    for i in [0, 10, 20]:
        flags[i] = "N"
    session = replace(session, logger_fields={"GPS_STATE": flags})
    obs = gps_observations(session, session.elapsed_time_s)
    assert np.flatnonzero(obs.position_new).tolist() == [0, 10, 20]
    assert np.flatnonzero(obs.speed_new).tolist() == [0, 10, 20]
    flags[10] = "I"
    obs = gps_observations(session, session.elapsed_time_s)
    assert not obs.available[10] and not obs.position_new[10]


def test_same_epoch_does_not_duplicate_a_new_flag(log_file):
    session = LegacyTxtImporter().load(log_file)
    epoch = replace(
        session.channels["NR"] if "NR" in session.channels else session.channels["ACC_X"],
        name="GPS_FIX_ID",
        samples=np.ones(len(session.nr)),
        valid=np.ones(len(session.nr), dtype=bool),
    )
    session = replace(
        session,
        channels={**session.channels, "GPS_FIX_ID": epoch},
        logger_fields={"GPS_STATE": ["N"] * len(session.nr)},
    )
    obs = gps_observations(session, session.elapsed_time_s)
    assert np.flatnonzero(obs.position_new).tolist() == [0]


def test_calibration_recovers_rotation_and_bias_without_standstill(navigation_data):
    session, truth = navigation_data
    arrays = {
        name: (id(c), hashlib.sha256(c.samples.tobytes()).digest())
        for name, c in session.channels.items()
    }
    result = estimate_navigation(
        session, NavigationConfig(gps_position_sigma_m=1, gps_altitude_sigma_m=2)
    )
    calibration = result.report["calibration"]
    error = np.degrees(
        np.linalg.norm(
            rotation_log(np.array(calibration["rotation_sensor_to_vehicle"]) @ truth["mounting"].T)
        )
    )
    print("Calibration error degrees", error, "report", calibration)
    assert error < 3.0
    np.testing.assert_allclose(calibration["acceleration_bias_sensor"], truth["ba"], atol=0.15)
    np.testing.assert_allclose(calibration["gyro_bias_sensor"], truth["bg"], atol=0.004)
    assert all(name.startswith("CALC_") for name in result.channels)
    assert all(
        channel.description and "CALC" in channel.source for channel in result.channels.values()
    )
    assert not result.channels["CALC_LAT"].valid[:100].any()
    assert result.channels["CALC_LAT"].valid[1450:1700].all()
    assert np.nanmedian(result.channels["CALC_VEL_X"].samples) > 0
    assert result.report["vehicle_frame"] == "FLU"
    assert result.report["calibration"]["vehicle_frame"] == "FLU"
    assert all(c.calculation["kind"] == "navigation_eskf_v2" for c in result.channels.values())
    expected_bias = truth["mounting"] @ truth["bg"]
    for j, axis in enumerate("XYZ"):
        assert np.nanmedian(result.channels["CALC_GYRO_BIAS_" + axis].samples) == pytest.approx(
            np.degrees(expected_bias[j]), abs=0.3
        )
    assert (result.channels["CALC_NAV_STATUS"].samples[1450:1700] == 2).all()
    assert arrays == {
        name: (id(c), hashlib.sha256(c.samples.tobytes()).digest())
        for name, c in session.channels.items()
    }
    assert session.navigation is None  # Pure calculation; publication is atomic in the GUI.
    clock = result.channels["CALC_TIME_S"].samples
    lat, lon = result.channels["CALC_LAT"].samples, result.channels["CALC_LON"].samples
    estimated = np.column_stack((np.radians(lon) * 6378137, np.radians(lat) * 6335439.3272928))
    errors = np.linalg.norm(estimated - truth["position"][:, :2], axis=1)
    print(
        "GPS dropout error",
        float(np.nanmax(errors[(clock >= 28) & (clock < 35)])),
        "overall RMS",
        float(np.sqrt(np.nanmean(errors**2))),
    )
    assert np.nanmax(errors[(clock >= 28) & (clock < 35)]) < 10
    assert np.sqrt(np.nanmean(errors**2)) < 3
    aided = (
        np.array([text == "N" for text in session.logger_fields["GPS_STATE"]])
        & result.channels["CALC_LAT"].valid
    )
    raw_errors = np.linalg.norm(
        truth["measured_position"][:, :2] - truth["position"][:, :2], axis=1
    )
    assert np.sqrt(np.mean(errors[aided] ** 2)) < np.sqrt(np.mean(raw_errors[aided] ** 2))


def test_motion_induced_loads_have_opposite_signs_g_units_and_no_gravity(navigation_data):
    session, truth = navigation_data
    result = estimate_navigation(
        session, NavigationConfig(gps_position_sigma_m=1, gps_altitude_sigma_m=2)
    )
    # Independent world-trajectory derivative, rotated into forward/left/up.
    body_acceleration = np.einsum("nji,nj->ni", truth["rotations"], truth["acceleration"])
    for j, axis in enumerate("XYZ"):
        load = result.channels["CALC_G_LOAD_" + axis]
        filtered = result.channels["CALC_G_LOAD_LP_" + axis]
        assert filtered.unit == "g" and filtered.group == "Calculated"
        assert filtered.valid[1450:1700].all()
        assert not filtered.valid[:100].any()
        assert "5 Hz" in filtered.description
        acceleration = result.channels["CALC_ACC_" + axis]
        assert load.unit == "g" and load.group == "Calculated"
        assert "gravity excluded" in load.description.lower()
        np.testing.assert_array_equal(load.valid, acceleration.valid)
        np.testing.assert_allclose(load.samples, -acceleration.samples / 9.80665)
        expected = -body_acceleration[:, j] / 9.80665
        assert np.nanmean(np.abs(load.samples - expected)) < 0.015
        assert not load.valid[:100].any()
        assert load.valid[1450:1700].all()  # Same GPS-loss prediction as acceleration.
    time = truth["time"]
    assert np.nanmean(result.channels["CALC_G_LOAD_X"].samples[(time >= 4) & (time <= 7)]) < 0
    assert np.nanmean(result.channels["CALC_G_LOAD_X"].samples[(time >= 13) & (time <= 17)]) > 0
    assert np.nanmean(result.channels["CALC_G_LOAD_Y"].samples[(time >= 4) & (time <= 7)]) < 0
    assert np.nanmean(result.channels["CALC_G_LOAD_Y"].samples[(time >= 22) & (time <= 25)]) > 0
    assert np.nanmax(np.abs(result.channels["CALC_G_LOAD_Z"].samples)) < 0.03
    assert result.report["output_prefix"] == "CALC_"
    assert result.report["load_smoothing"]["cutoff_hz"] == 5
    assert result.report["g_load"]["includes_gravity"] is False
    assert result.report["g_load"]["gravity_m_s2"] == 9.80665
    assert not any(name.startswith("EST_") for name in result.channels)


def test_unobservable_constant_driving_rejected():
    f = np.tile([0, 0, 9.80665], (100, 1))
    w = np.zeros_like(f)
    with pytest.raises(ValueError, match="not observable"):
        fit_mounting(f, w, f, w)


def test_straight_constant_speed_with_changing_grade_can_calibrate(tmp_path):
    truth = trajectory(turns=False, variable_speed=False, slope_amplitude=0.08, slope_period_s=6)
    session = LegacyTxtImporter().load(write_demo(tmp_path / "hills.TXT", truth))
    result = estimate_navigation(
        session, NavigationConfig(gps_position_sigma_m=1, gps_altitude_sigma_m=2)
    )
    rotation = np.array(result.report["calibration"]["rotation_sensor_to_vehicle"])
    assert np.degrees(np.linalg.norm(rotation_log(rotation @ truth["mounting"].T))) < 3


@pytest.mark.parametrize("vector", [[0, 0, np.pi], [0, np.pi / 2, 0], [0.4, -0.2, 1.0]])
def test_mounting_axis_permutations_and_biases(vector):
    rng = np.random.default_rng(2)
    target_f = rng.normal(0, 0.5, (200, 3)) + [0, 0, 9.80665]
    target_w = rng.normal(0, 0.05, (200, 3))
    rotation = rotation_exp(vector)
    ba, bg = np.array([0.05, -0.03, 0.02]), np.array([0.02, -0.04, 0.01])
    actual = fit_mounting(target_f @ rotation + ba, target_w @ rotation + bg, target_f, target_w)
    np.testing.assert_allclose(actual[0], rotation, atol=1e-10)
    np.testing.assert_allclose(actual[1], ba, atol=1e-10)
    np.testing.assert_allclose(actual[2], bg, atol=1e-10)


def test_no_imu_gap_is_bridged_and_est_gpx_has_separate_segments(navigation_data, tmp_path):
    source, _truth = navigation_data
    broken = source.channels["GYRO_X"]
    valid = broken.valid.copy()
    valid[2000:2005] = False
    session = replace(source, channels={**source.channels, "GYRO_X": replace(broken, valid=valid)})
    result = estimate_navigation(session)
    assert not result.channels["CALC_LAT"].valid[2000:2005].any()
    for axis in "XYZ":
        assert not result.channels["CALC_G_LOAD_" + axis].valid[2000:2005].any()
        assert not result.channels["CALC_G_LOAD_LP_" + axis].valid[1980:2025].any()
    assert result.channels["CALC_LAT"].valid[2200]
    assert result.report["initializations"] == 2
    session = replace(session, navigation=result, channels={**session.channels, **result.channels})
    path = tmp_path / "est.gpx"
    export_estimated_gpx(session, path)
    root = ET.parse(path).getroot()
    assert len(root.findall(".//{*}trkseg")) == 2
    assert root.get("creator") == "LapTune CALC"
    assert "X forward, Y left, Z up" in root.find(".//{*}desc").text
    assert not root.findall(".//{*}time")
    assert len(root.findall(".//{*}trkpt")) == result.channels["CALC_LAT"].valid.sum()
    raw_path = tmp_path / "raw.gpx"
    export_gpx(session, raw_path)
    assert len(ET.parse(raw_path).findall(".//{*}trkpt")) == len(source.gps_fix_samples)
    csv_path = tmp_path / "out.csv"
    export_csv(session, csv_path, ["ACC_X", "CALC_ACC_X"])
    assert "CALC_ACC_X [m/s²]" in csv_path.read_text(encoding="utf-8").splitlines()[0]


@pytest.mark.parametrize("name", ["CALC_LAT", "CALC_G_LOAD_Y"])
def test_original_calc_named_channel_is_protected(navigation_data, name):
    source, _ = navigation_data
    raw = replace(source.channels["ACC_X"], name=name)
    session = replace(source, channels={**source.channels, name: raw})
    with pytest.raises(ValueError, match="original channels will not be replaced"):
        estimate_navigation(session)
    assert session.channels[name] is raw


def test_legacy_prefix_in_an_original_channel_is_not_renamed(navigation_data):
    source, _ = navigation_data
    raw = replace(source.channels["ACC_X"], name="EST_LAT")
    values = raw.samples.copy()
    session = replace(source, channels={**source.channels, "EST_LAT": raw})
    result = estimate_navigation(session)
    assert session.channels["EST_LAT"] is raw
    np.testing.assert_array_equal(raw.samples, values)
    assert "EST_LAT" not in result.channels and "CALC_LAT" in result.channels


def test_gps_innovation_rejects_a_spike_and_reacquires(navigation_data):
    source, truth = navigation_data
    channel = source.channels["GPS_LON"]
    samples = channel.samples.copy()
    samples[2100] += np.degrees(
        12 / 6378137
    )  # Under the physical jump cutoff, above innovation gate.
    session = replace(
        source, channels={**source.channels, "GPS_LON": replace(channel, samples=samples)}
    )
    result = estimate_navigation(
        session, NavigationConfig(gps_position_sigma_m=1, gps_altitude_sigma_m=2)
    )
    assert result.report["rejected_position_updates"] >= 1
    assert result.channels["CALC_GPS_AGE_S"].samples[2100] > 0
    assert result.channels["CALC_GPS_AGE_S"].samples[2110] == 0
    x = np.radians(result.channels["CALC_LON"].samples[2110]) * 6378137
    assert abs(x - truth["position"][2110, 0]) < 2


def test_covariance_increases_during_gps_loss(navigation_data):
    source, _ = navigation_data
    result = estimate_navigation(source)
    radius = result.channels["CALC_POS_95_M"].samples
    assert radius[1740] > radius[1420]
    assert radius[1900] < radius[1740]


def test_hardware_clock_reset_starts_a_new_navigation_segment(navigation_data):
    source, _ = navigation_data
    channel = source.channels["T_IMU_US"]
    values = channel.samples.copy()
    values[2000:] -= 40000000
    session = replace(
        source, channels={**source.channels, "T_IMU_US": replace(channel, samples=values)}
    )
    result = estimate_navigation(session)
    assert not result.channels["CALC_LAT"].valid[2000]
    assert result.report["initializations"] == 2


def test_gravity_projection_for_tilted_sensor():
    rotation = rotation_exp([0.2, -0.15, 0.7])
    filt = InertialFilter(
        np.zeros(3), np.zeros(3), rotation, np.zeros(3), np.zeros(3), NavigationConfig()
    )
    for _ in range(100):
        filt.predict(rotation.T @ [0, 0, 9.80665], np.zeros(3), 0.01)
    np.testing.assert_allclose(filt.p, 0, atol=1e-10)
    np.testing.assert_allclose(filt.v, 0, atol=1e-10)


@pytest.mark.local
@pytest.mark.parametrize("filename", ["LOG0013.TXT", "LOG0023.TXT"])
def test_optional_private_navigation(filename):
    path = Path("data/local") / filename
    if not path.exists():
        pytest.skip("Private navigation log intentionally excluded from Git")
    session = LegacyTxtImporter().load(path)
    result = estimate_navigation(session)
    assert result.channels["CALC_LAT"].valid.sum() > 1000
    rotation = result.report["calibration"]["rotation_sensor_to_vehicle"]
    assert np.linalg.det(rotation) == pytest.approx(1)
    assert not any(name.startswith("CALC_") for name in session.channels)


def test_cancellation_does_not_publish_partial_channels(navigation_data):
    source, _ = navigation_data
    names = list(source.channels)

    def cancel(percent):
        if percent >= 50:
            raise InterruptedError("cancel test")

    with pytest.raises(InterruptedError, match="cancel test"):
        estimate_navigation(source, progress=cancel)
    assert list(source.channels) == names and source.navigation is None


@pytest.mark.parametrize(
    "force, expected_v, expected_p",
    [
        ([1.0, 0, 9.80665], [11, 0, 0], [10.5, 0, 0]),
        ([-1.0, 0, 9.80665], [9, 0, 0], [9.5, 0, 0]),
        ([0, 2.0, 9.80665], [10, 2, 0], [10, 1, 0]),
        ([0, -2.0, 9.80665], [10, -2, 0], [10, -1, 0]),
    ],
    ids=["accelerating-forward", "braking", "accelerating-left", "accelerating-right"],
)
def test_vehicle_acceleration_signs(force, expected_v, expected_p):
    config = NavigationConfig()
    filt = InertialFilter(
        np.zeros(3),
        np.array([10.0, 0, 0]),
        track_rotation(np.array([10.0, 0, 0])),
        np.zeros(3),
        np.zeros(3),
        config,
    )
    for _ in range(100):
        filt.predict(np.array(force), np.zeros(3), 0.01)
    np.testing.assert_allclose(filt.v, expected_v, atol=1e-9)
    np.testing.assert_allclose(filt.p, expected_p, atol=1e-9)
    assert np.linalg.eigvalsh(filt.covariance).min() >= -1e-10


@pytest.mark.parametrize(
    "velocity, heading, pitch",
    [
        ([10.0, 0, 0], 90, 0),
        ([0, 10.0, 0], 0, 0),
        ([-10.0, 0, 0], 270, 0),
        ([0, -10.0, 0], 180, 0),
        ([10.0, 0, 2.0], 90, np.degrees(np.arctan2(2, 10))),
        ([0, 10.0, -2.0], 0, -np.degrees(np.arctan2(2, 10))),
    ],
    ids=["east", "north", "west", "south", "uphill", "downhill"],
)
def test_forward_heading_and_front_up_pitch(velocity, heading, pitch):
    rotation = track_rotation(np.array(velocity))
    np.testing.assert_allclose(attitude_angles(rotation), [0, pitch, heading], atol=1e-9)
    np.testing.assert_allclose(rotation.T @ velocity, [np.linalg.norm(velocity), 0, 0], atol=1e-9)
    assert np.linalg.det(rotation) == pytest.approx(1)
    assert attitude_angles(rotation_exp([0, -0.1, 0]))[1] > 0


@pytest.mark.parametrize("yaw_rate", [0.1, -0.1], ids=["left-turn", "right-turn"])
def test_turns_have_correct_lateral_and_yaw_signs(yaw_rate):
    filt = InertialFilter(
        np.zeros(3), np.array([10.0, 0, 0]), np.eye(3), np.zeros(3), np.zeros(3), NavigationConfig()
    )
    for _ in range(100):
        filt.predict(np.array([0, 10 * yaw_rate, 9.80665]), np.array([0, 0, yaw_rate]), 0.01)
    np.testing.assert_allclose(
        filt.v[:2], [10 * np.cos(yaw_rate), 10 * np.sin(yaw_rate)], atol=1e-5
    )
    np.testing.assert_allclose(
        filt.p[:2],
        [10 / yaw_rate * np.sin(yaw_rate), 10 / yaw_rate * (1 - np.cos(yaw_rate))],
        atol=1e-5,
    )
    assert filt.v[1] * yaw_rate > 0


def test_body_frame_change_preserves_world_solution():
    half_turn = np.diag([-1.0, -1.0, 1.0])
    rotation = rotation_exp([0.05, -0.1, 0.7])
    ba, bg = np.array([0.02, -0.03, 0.04]), np.array([0.001, -0.002, 0.001])
    velocity = np.array([12.0, 4.0, 0.2])
    flu = InertialFilter(np.zeros(3), velocity, rotation, ba, bg, NavigationConfig())
    legacy = InertialFilter(
        np.zeros(3),
        velocity,
        rotation @ half_turn,
        half_turn @ ba,
        half_turn @ bg,
        NavigationConfig(),
    )
    h = np.zeros((2, 15))
    h[:, :2] = np.eye(2)
    for i in range(100):
        force = np.array([0.2 * np.cos(i / 20), 0.3 * np.sin(i / 20), 9.80665])
        gyro = np.array([0.01, -0.02, 0.03 * np.cos(i / 10)])
        flu.predict(force, gyro, 0.02)
        legacy.predict(half_turn @ force, half_turn @ gyro, 0.02)
        if i % 10 == 0:
            measurement = flu.p[:2] + [0.1, -0.2]
            assert flu.update(measurement - flu.p[:2], h, np.eye(2))
            assert legacy.update(measurement - legacy.p[:2], h, np.eye(2))
    np.testing.assert_allclose(flu.p, legacy.p, atol=1e-9)
    np.testing.assert_allclose(flu.v, legacy.v, atol=1e-9)
    np.testing.assert_allclose(flu.rotation, legacy.rotation @ half_turn, atol=1e-9)
    np.testing.assert_allclose(flu.ba, half_turn @ legacy.ba, atol=1e-9)
    np.testing.assert_allclose(flu.bg, half_turn @ legacy.bg, atol=1e-9)
    np.testing.assert_allclose(flu.covariance[:6, :6], legacy.covariance[:6, :6], atol=1e-9)


def test_aligned_sensor_calibrates_without_an_unwanted_half_turn(tmp_path):
    truth = trajectory()
    gyro_body = (truth["gyro"] - truth["bg"]) @ truth["mounting"].T
    aligned = {
        **truth,
        "force": truth["force_body"] + truth["ba"],
        "gyro": gyro_body + truth["bg"],
        "mounting": np.eye(3),
    }
    session = LegacyTxtImporter().load(write_demo(tmp_path / "aligned.TXT", aligned))
    originals = {name: channel.samples.copy() for name, channel in session.channels.items()}
    result = estimate_navigation(
        session, NavigationConfig(gps_position_sigma_m=1, gps_altitude_sigma_m=2)
    )
    mounting = np.array(result.report["calibration"]["rotation_sensor_to_vehicle"])
    assert np.degrees(np.linalg.norm(rotation_log(mounting))) < 3
    time = truth["time"]
    assert np.nanmean(result.channels["CALC_ACC_X"].samples[(time >= 4) & (time <= 7)]) > 0
    assert np.nanmean(result.channels["CALC_ACC_X"].samples[(time >= 13) & (time <= 17)]) < 0
    assert np.nanmean(result.channels["CALC_ACC_Y"].samples[(time >= 4) & (time <= 7)]) > 0
    for name, samples in originals.items():
        np.testing.assert_array_equal(session.channels[name].samples, samples)


@pytest.mark.gui
def test_navigation_wizard_worker_map_export_and_cancel():
    environment = os.environ.copy()
    if sys.platform == "linux":
        environment.update(
            QT_QPA_PLATFORM="offscreen",
            QT_QUICK_BACKEND="software",
            QT_OPENGL="software",
            QTWEBENGINE_DISABLE_SANDBOX="1",
            QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-dev-shm-usage",
        )
    result = subprocess.run(
        [sys.executable, "tools/check_navigation.py"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NAVIGATION_GUI_PASS" in result.stdout


@pytest.mark.gui
def test_navigation_cli_smoke_used_by_frozen_executable(tmp_path):
    path = write_demo(tmp_path / "navigation.TXT")
    environment = os.environ.copy()
    if sys.platform == "linux":
        environment.update(
            QT_QPA_PLATFORM="offscreen",
            QT_QUICK_BACKEND="software",
            QT_OPENGL="software",
            QTWEBENGINE_DISABLE_SANDBOX="1",
            QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-dev-shm-usage",
        )
    output = tmp_path / "smoke.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "telemetry_viewer",
            "--smoke-navigation",
            str(path),
            "--smoke-result",
            str(output),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GUI_NAVIGATION_SMOKE_PASS" in result.stdout
