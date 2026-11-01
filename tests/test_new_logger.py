"""Synthetic regressions for GPS clock loss, faster refresh and MSL altitude."""

import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from telemetry_viewer.exporters.files import export_csv, export_gpx
from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter
from telemetry_viewer.processing.signals import SignalRecipe, calculate_signal
from telemetry_viewer.processing.timebase import build_timebase

HEADER = "NR;DATA;CZAS;ACC_X;GPS_LAT;GPS_LON;GPS_SPEED_KMH;GPS_ALT_MSL_M"


@pytest.fixture
def modern_log(tmp_path):
    # Synthetic equatorial route only. Clock/GPS loss at startup, in the middle and at the end.
    rows = [HEADER]
    origin = datetime(2026, 1, 1, 23, 59, 59, 960000)
    for i in range(101):
        if i < 4 or 45 <= i < 55 or i >= 98:
            rows.append(f"{100 + i};;;0.1;;;;")
            continue
        stamp = origin + timedelta(seconds=i * 0.01)
        fix = i // 20  # Observable coordinate changes at 5 Hz, not the IMU's row rate.
        # Altitude can update independently, including while coordinates stay unchanged.
        altitude = "" if i < 7 else str(-2 + i // 10)
        rows.append(
            f"{100 + i};{stamp:%Y-%m-%d};{stamp:%H:%M:%S.%f};0.1;"
            f"0;{fix * 2 / 111195.0802335329};36;{altitude}"
        )
    path = tmp_path / "modern.TXT"
    path.write_text("\n".join(rows), encoding="utf-8")
    return path


def test_clock_loss_preserves_imu_and_raw_gps_gaps(modern_log):
    before = hashlib.sha256(modern_log.read_bytes()).digest()
    session = LegacyTxtImporter().load(modern_log)
    assert len(session.nr) == 101
    np.testing.assert_allclose(session.elapsed_time_s, np.arange(101) / 100, atol=1e-12)
    assert session.channels["ACC_X"].valid.all()
    altitude = session.channels["GPS_ALT_MSL_M"]
    assert (altitude.group, altitude.unit, altitude.frequency_hz) == ("GPS", "m", None)
    missing = np.r_[0:4, 45:55, 98:101]
    assert not session.channels["GPS_LAT"].valid[missing].any()
    assert np.isnan(altitude.samples[missing]).all()
    assert not altitude.valid[:7].any()
    assert altitude.samples[7] == -2  # Below sea level is valid, as is zero metres.
    assert altitude.valid[20] and altitude.samples[20] == 0
    assert session.timestamp_raw[0] == " "
    assert session.timestamp_raw[4] == "2026-01-02 00:00:00.000000"
    assert sum(i.code == "estimated_time" for i in session.issues) == 3
    assert not any(i.code in ("invalid_number", "invalid_time") for i in session.issues)
    assert "estimates are not recorded" in session.metadata.time_correction
    # Do not bridge GPS loss or assume a fixed 1 Hz refresh rate.
    assert sum(session.gps_segment_start) == 2
    assert session.gps_fix_samples.tolist() == [4, 7, 10, 20, 30, 40, 55, 60, 70, 80, 90]
    assert not any(i.code == "gps_jump" for i in session.issues)
    assert before == hashlib.sha256(modern_log.read_bytes()).digest()


@pytest.mark.parametrize(
    "raw,expected,estimated",
    [
        ([" ", " ", "00.000", "00.010"], [0, 0.01, 0.02, 0.03], True),
        (["00.000", " ", " ", "00.060"], [0, 0.02, 0.04, 0.06], True),
        (["00.000", "00.020", " ", " "], [0, 0.02, 0.04, 0.06], True),
        ([" ", " "], [0, 1e-6], False),
        ([" ", "00.000", " "], [0, 1e-6, 2e-6], False),
        (["00.020", " ", "00.000"], [0, 1e-6, 2e-6], False),
    ],
)
def test_missing_clock_estimates_and_insufficient_anchors(raw, expected, estimated):
    raw = ["2026-01-01 00:00:" + v if v.strip() else v for v in raw]
    time, issues = build_timebase(raw, list(range(2, len(raw) + 2)))
    np.testing.assert_allclose(time, expected, atol=1e-12)
    assert any(i.code == "estimated_time" for i in issues) == estimated
    assert not any(i.code == "invalid_time" for i in issues)


def test_altitude_only_updates_and_invalid_readings(tmp_path):
    path = tmp_path / "altitude.TXT"
    path.write_text(
        HEADER
        + "\n"
        + "\n".join(
            f"{i};2026-01-01;00:00:0{i}.000;0;0;0;0;{alt}"
            for i, alt in enumerate(["", "-1.5", "-1.5", "0", "inf", "oops", "2.5"])
        )
    )
    session = LegacyTxtImporter().load(path)
    altitude = session.channels["GPS_ALT_MSL_M"]
    assert altitude.valid.tolist() == [False, True, True, True, False, False, True]
    assert session.gps_fix_samples.tolist() == [0, 1, 3, 4, 6]
    assert not session.distance_gps_m.any()
    assert sum(i.code == "invalid_number" for i in session.issues) == 2
    assert sum(i.code == "missing_measurement" for i in session.issues) == 1


def test_altitude_csv_gpx_and_calculation(modern_log, tmp_path):
    session = LegacyTxtImporter().load(modern_log)
    csv_path, gpx_path = tmp_path / "out.csv", tmp_path / "out.gpx"
    export_csv(session, csv_path, ["GPS_ALT_MSL_M"])
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["GPS_ALT_MSL_M [m]"] == ""
    assert float(rows[7]["GPS_ALT_MSL_M [m]"]) == -2
    assert rows[45]["GPS_ALT_MSL_M [m]"] == ""
    metadata = json.loads(csv_path.with_suffix(".csv.metadata.json").read_text())
    assert metadata["channels"]["GPS_ALT_MSL_M"]["unit"] == "m"
    assert "empty timestamps" in metadata["session"]["time_correction"]
    export_gpx(session, gpx_path)
    points = ET.parse(gpx_path).findall(".//{*}trkpt")
    assert points[0].find("{*}ele") is None
    assert [float(p.find("{*}ele").text) for p in points[1:]] == [-2, -1, 0, 1, 2, 3, 4, 5, 6, 7]
    filtered = calculate_signal(
        session.channels,
        SignalRecipe("Altitude_filtered", "lowpass", ("GPS_ALT_MSL_M",), cutoff_hz=1),
    )
    assert filtered.unit == "m" and filtered.group == "Calculated"
    assert not filtered.valid[:7].any()


def test_mixed_firmware_folder_keeps_altitude_optional_and_clock_continuous(tmp_path):
    header = "NR;DATA;CZAS;ACC_X;GPS_LAT;GPS_LON;GPS_SPEED_KMH"
    (tmp_path / "LOG1.TXT").write_text(header + "\n1;2026-01-01;00:00:00.000;0;0;0;36\n")
    (tmp_path / "LOG2.TXT").write_text(
        HEADER + "\n2;;;0;;;;\n3;2026-01-01;00:00:00.400;0;0;0.00001;36;123\n"
    )
    session = LegacyTxtImporter().load(tmp_path)
    np.testing.assert_allclose(session.elapsed_time_s, [0, 0.2, 0.4])
    assert session.channels["GPS_ALT_MSL_M"].valid.tolist() == [False, False, True]
    assert not any(
        i.code == "missing_channel" and "GPS_ALT_MSL_M" in i.message for i in session.issues
    )
    assert any(
        i.code == "estimated_time" and i.source == "LOG2.TXT" and i.line == 2
        for i in session.issues
    )


@pytest.mark.gui
def test_new_logger_gui_altitude_and_route_gaps(modern_log):
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
        [sys.executable, "tools/check_new_logger.py", str(modern_log)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NEW_LOGGER_PASS" in result.stdout


@pytest.mark.local
def test_optional_private_new_logger():
    path = Path("data/local/LOG0013.TXT")
    if not path.exists():
        pytest.skip("Private new-format log intentionally excluded from Git")
    session = LegacyTxtImporter().load(path)
    assert len(session.nr) == 18654
    assert session.channels["GPS_ALT_MSL_M"].valid.sum() == 14185
    assert sum(session.gps_segment_start) == 3
    assert np.all(np.diff(session.elapsed_time_s) > 0)
    assert len(session.gps_fix_samples) > 650
    assert sum(i.code == "estimated_time" for i in session.issues) == 3
    assert not any(i.code == "invalid_number" for i in session.issues)
