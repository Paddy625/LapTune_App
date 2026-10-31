import csv
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from telemetry_viewer.domain.models import Channel
from telemetry_viewer.exporters.files import export_csv, export_gpx
from telemetry_viewer.importers.base import ImportErrorDetail
from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter
from telemetry_viewer.processing.gps import gps_distances, haversine_m
from telemetry_viewer.processing.timebase import build_timebase


def test_import_midnight_distance_and_types(log_file):
    s = LegacyTxtImporter().load(log_file)
    assert len(s.nr) == 301 and s.nr[0] == 70000
    assert len(s.channels) == 9
    assert s.channels["ACC_X"].samples.dtype == np.float64
    assert s.timestamp_raw[0].startswith("2026-01-01")
    assert s.timestamp_raw[-1].startswith("2026-01-02")
    assert s.elapsed_time_s[-1] == pytest.approx(3)
    assert len(s.gps_fix_samples) == 4
    assert s.distance_speed_m[-1] == pytest.approx(30)
    assert s.distance_gps_m[-1] == pytest.approx(30, abs=0.001)
    assert s.distance_m is s.distance_speed_m
    s.distance_method = "gps"
    assert s.distance_m is s.distance_gps_m


def test_29_duplicates_3_backsteps_are_preserved():
    raw = ["2026-01-01 00:00:01.000"] * 30 + ["2026-01-01 00:00:00.000"] * 3
    time, issues = build_timebase(raw, list(range(2, 35)))
    assert len(time) == 33 and np.all(np.diff(time) > 0)
    assert sum(i.code == "time_correction" for i in issues) == 32
    assert raw[-1] == "2026-01-01 00:00:00.000"


def test_time_invalid_gap_and_midnight():
    raw = ["2026-01-01 23:59:59.900", "bad", "2026-01-02 00:00:00.000", "2026-01-02 00:00:10.000"]
    time, issues = build_timebase(raw, [2, 3, 4, 5])
    np.testing.assert_allclose(time, [0, 1e-6, 0.1, 10.1])
    assert {i.code for i in issues} == {"time_correction", "invalid_time", "time_gap"}


@pytest.mark.parametrize(
    "header,row",
    [
        ("NR;DATA;CZAS;ACC_X", "1;2026-01-01;00:00:00.000;1"),
        ("NR;DATA;CZAS;GPS_LAT;GPS_LON", "1;2026-01-01;00:00:00.000;0;0"),
        ("NR;DATA;CZAS;GPS_SPEED_KMH", "1;2026-01-01;00:00:00.000;36"),
    ],
)
def test_missing_optional_and_single_row(tmp_path, header, row):
    p = tmp_path / "one.txt"
    p.write_text(header + "\n" + row)
    s = LegacyTxtImporter().load(p)
    assert len(s.nr) == 1 and s.distance_m[0] == 0
    assert any(i.code == "missing_channel" for i in s.issues)


@pytest.mark.parametrize("text", ["", "NR,DATA,CZAS\n1,a,b", "NR;DATA\n1;a", "NR;DATA;CZAS\n"])
def test_bad_headers_and_empty(tmp_path, text):
    p = tmp_path / "bad.txt"
    p.write_text(text)
    with pytest.raises(ImportErrorDetail):
        LegacyTxtImporter().load(p)


def test_invalid_values_and_line_diagnostics(tmp_path):
    p = tmp_path / "bad.txt"
    p.write_text(
        "NR;DATA;CZAS;GPS_LAT;GPS_LON;ACC_X\n"
        "1;2026-01-01;bad;91;0;inf\n"
        "2;2026-01-01;00:00:01.000;0;nan;hello\n"
        "malformed\n"
    )
    s = LegacyTxtImporter().load(p)
    assert len(s.nr) == 2 and not len(s.gps_fix_samples)
    assert not s.channels["ACC_X"].valid.any()
    assert not s.channels["GPS_LAT"].valid[0]
    assert any(i.line == 4 and i.code == "malformed_row" for i in s.issues)
    assert any(i.line == 2 and i.code == "invalid_time" for i in s.issues)


def test_repeated_gps_and_jump():
    lat = np.array([0.0, 0, 0, 50, 0, np.nan, 0])
    lon = np.array([0.0, 0, 0, 50, 0, np.nan, 0.0001])
    rows, starts, distance, issues = gps_distances(lat, lon, np.zeros(7), np.arange(7), range(7))
    assert rows.tolist() == [0, 4, 6]
    assert starts.tolist() == [True, False, True]
    assert distance[-1] == 0
    assert sum(i.code == "gps_jump" for i in issues) == 1
    assert haversine_m(0, 0, 0, 1) == pytest.approx(111195.0802, abs=0.001)


def test_variable_gps_update_rate(tmp_path):
    p = tmp_path / "variable.txt"
    p.write_text(
        "NR;DATA;CZAS;GPS_LAT;GPS_LON;GPS_SPEED_KMH\n"
        + "\n".join(
            f"{i};2026-01-01;00:00:0{i}.000;0;{lon};{speed}"
            for i, (lon, speed) in enumerate(
                [(0, 36), (0, 36), (0, 18), (0.0001, 18), (0.0002, 18), (0.0002, 18)]
            )
        )
    )
    s = LegacyTxtImporter().load(p)
    assert s.gps_fix_samples.tolist() == [0, 2, 3, 4]
    assert s.distance_speed_m[-1] == pytest.approx(35)


def test_axes_mapping_ambiguity_and_async_channels(log_file):
    s = LegacyTxtImporter().load(log_file)
    for mode in ("time", "distance"):
        assert s.sample_at(s.axis(mode)[150], mode) == 150
    assert s.fix_for_sample(149) == 100
    assert s.fix_for_sample(151) == 200
    assert s.sample_for_location(0, s.channels["GPS_LON"].samples[200], 200) == 200
    s.channels["GPS_LON"].samples[300] = 0
    assert s.sample_for_location(0, 0, 300) == 300
    channel = Channel(
        "RPM",
        "rpm",
        "CAN",
        "CAN",
        np.array([1000.0, 2000.0]),
        np.array([0.0, 1.5]),
        np.array([True, True]),
        2 / 3,
        "hold",
    )
    np.testing.assert_allclose(s.channel_axis(channel, "distance"), [0, 15])
    s.distance_speed_m[:] = 0
    assert s.sample_at(0, "distance", active=240) == 240


def test_exports_units_raw_time_and_no_fabrication(log_file, tmp_path):
    s = LegacyTxtImporter().load(log_file)
    p = tmp_path / "out.csv"
    export_csv(s, p, ["ACC_X"])
    with p.open(newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 302 and rows[0][-1] == "ACC_X [g (assumed)]"
    assert rows[1][2] == s.timestamp_raw[0]
    metadata = json.loads(p.with_suffix(".csv.metadata.json").read_text())
    assert metadata["session"]["timezone"] == ""
    gpx = tmp_path / "out.gpx"
    export_gpx(s, gpx)
    root = ET.parse(gpx).getroot()
    assert len(root.findall(".//{*}trkpt")) == 4
    assert not root.findall(".//{*}time")
    assert len(root.findall(".//{*}timestamp_raw")) == 4
    s.metadata.timezone = "UTC"
    export_gpx(s, gpx)
    assert ET.parse(gpx).find(".//{*}time").text.endswith("Z")


@pytest.mark.local
def test_optional_private_log():
    p = Path("data/local/LOG0023.TXT")
    if not p.exists():
        pytest.skip("Private log intentionally excluded from the repository")
    s = LegacyTxtImporter().load(p)
    assert len(s.nr) == 18640
    assert s.elapsed_time_s[-1] == pytest.approx(180.272, abs=0.01)
    assert 150 <= len(s.gps_fix_samples) <= 210
    assert 3300 < s.distance_m[-1] < 4100
    assert np.all(np.diff(s.elapsed_time_s) > 0)
