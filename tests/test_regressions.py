import csv

import numpy as np
import pytest

from telemetry_viewer.domain.models import Channel
from telemetry_viewer.exporters.files import export_csv, export_gpx
from telemetry_viewer.importers.base import ImportErrorDetail
from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter
from telemetry_viewer.processing.gps import gps_distances, integrate_speed


def test_speed_refresh_does_not_reset_coordinate_jump_clock():
    # Position 1 Hz, speed 10 Hz: a 20 m movement in 1 s must not look like 200 m/s.
    time = np.arange(11) / 10
    lat = np.zeros(11)
    lon = np.r_[np.zeros(10), 20 / 111195.0802335329]
    speed = np.arange(11) + 72.0
    fixes, _, distance, issues = gps_distances(lat, lon, speed, time, range(11))
    assert len(fixes) == 11
    assert distance[-1] == pytest.approx(20)
    assert not issues


def test_integration_irregular_dt_and_invalid_speed():
    actual = integrate_speed(np.array([36.0, 72, np.nan, -1, 36]), np.array([0.0, 0.1, 0.6, 1, 2]))
    np.testing.assert_allclose(actual, [0, 1, 11, 11, 11])


def test_future_channel_and_progress(tmp_path):
    p = tmp_path / "future.txt"
    p.write_text(
        "NR;DATA;CZAS;ENGINE_RPM\n90;2026-01-01;12:00:00.000;2300\n"
        "91;2026-01-01;12:00:00.050;2400\n"
    )
    progress = []
    s = LegacyTxtImporter().load(p, progress.append)
    assert s.channels["ENGINE_RPM"].samples.tolist() == [2300, 2400]
    assert s.channels["ENGINE_RPM"].group == "CAN / Other"
    assert progress[-1] == 100
    assert progress == sorted(progress)
    with pytest.raises(ValueError, match="no accepted GPS"):
        export_gpx(s, tmp_path / "no.gpx")


def test_no_manufactured_async_values(log_file, tmp_path):
    s = LegacyTxtImporter().load(log_file)
    s.channels["ENGINE_RPM"] = Channel(
        "ENGINE_RPM",
        "rpm",
        "CAN",
        "CAN / Other",
        np.array([1000.0, 2000.0]),
        np.array([0.0, 1.5]),
        np.ones(2, dtype=bool),
        interpolation="hold",
    )
    p = tmp_path / "async.csv"
    export_csv(s, p, ["ENGINE_RPM"])
    with p.open() as f:
        rows = list(csv.reader(f))
    assert rows[1][-1] == "1000.0" and rows[151][-1] == "2000.0"
    assert sum(bool(row[-1]) for row in rows[1:]) == 2


def test_invalid_encoding(tmp_path):
    p = tmp_path / "not_utf8.txt"
    p.write_bytes(b"NR;DATA;CZAS\n1;\xff;bad")
    with pytest.raises(ImportErrorDetail, match="encoding"):
        LegacyTxtImporter().load(p)
