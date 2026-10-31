import csv
import hashlib
import json
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from telemetry_viewer.exporters.files import export_csv, export_gpx
from telemetry_viewer.importers.base import ImportErrorDetail
from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter, folder_logs


@pytest.fixture
def log_folder(log_file, tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    header, *rows = log_file.read_text().splitlines()
    # Creation order differs from numeric filename order. Cuts repeat held GPS across files.
    for name, chunk in [
        ("LOG10.TXT", rows[220:]),
        ("LOG2.txt", rows[:110]),
        ("LOG003.TxT", rows[110:220]),
    ]:
        (folder / name).write_text("\n".join([header, *chunk]), encoding="utf-8")
    (folder / "notes.csv").write_text("not a log")
    (folder / "nested").mkdir()
    (folder / "nested" / "LOG1.txt").write_text("not part of the selected folder")
    return folder


def test_folder_equals_original_unsplit_session(log_folder, log_file):
    paths = folder_logs(log_folder)
    before = {p: hashlib.sha256(p.read_bytes()).digest() for p in paths}
    whole = LegacyTxtImporter().load(log_file)
    combined = LegacyTxtImporter().load(log_folder)
    assert [s.path.name for s in combined.source_files] == ["LOG2.txt", "LOG003.TxT", "LOG10.TXT"]
    assert [(s.start_row, s.end_row) for s in combined.source_files] == [
        (0, 110),
        (110, 220),
        (220, 301),
    ]
    assert combined.timestamp_raw == whole.timestamp_raw
    for name in (
        "nr",
        "row_index",
        "elapsed_time_s",
        "gps_fix_samples",
        "gps_segment_start",
        "distance_speed_m",
        "distance_gps_m",
    ):
        np.testing.assert_array_equal(getattr(combined, name), getattr(whole, name))
    for name, channel in whole.channels.items():
        np.testing.assert_array_equal(combined.channels[name].samples, channel.samples)
    assert len(combined.gps_fix_samples) == 4
    assert not combined.issues
    assert combined.source_for_sample(109).path.name == "LOG2.txt"
    assert combined.source_for_sample(110).path.name == "LOG003.TxT"
    assert combined.source_lines[110] == 2
    assert combined.source_lines[219] == 111
    assert combined.source_for_sample(301) is None
    assert before == {p: hashlib.sha256(p.read_bytes()).digest() for p in paths}


def test_numeric_order_ignores_prefix_and_includes_unnumbered(tmp_path):
    names = ["LOG10.txt", "Z2.TXT", "A100.txt", "LOG0003.txt", "notes.txt", "A3.TXT", "LOG1.csv"]
    for name in names:
        (tmp_path / name).touch()
    assert [p.name for p in folder_logs(tmp_path)] == [
        "Z2.TXT",
        "A3.TXT",
        "LOG0003.txt",
        "LOG10.txt",
        "A100.txt",
        "notes.txt",
    ]


@pytest.mark.parametrize("content", ["", "NR;DATA\n1;2026-01-01", "NR;DATA;CZAS\n"])
def test_invalid_file_aborts_and_identifies_source(log_folder, content):
    (log_folder / "LOG004.txt").write_text(content)
    with pytest.raises(ImportErrorDetail, match="LOG004.txt"):
        LegacyTxtImporter().load(log_folder)


def test_empty_folder_and_no_recursion(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "LOG1.txt").touch()
    with pytest.raises(ImportErrorDetail, match="no .txt files"):
        LegacyTxtImporter().load(tmp_path)


def test_union_of_columns_marks_absent_measurements_invalid(tmp_path):
    (tmp_path / "LOG1.txt").write_text("NR;DATA;CZAS;ACC_X\n1;2026-01-01;00:00:00.000;0.1\n")
    (tmp_path / "LOG2.txt").write_text("NR;DATA;CZAS;ENGINE_RPM\n2;2026-01-01;00:00:00.200;2400\n")
    s = LegacyTxtImporter().load(tmp_path)
    np.testing.assert_allclose(s.channels["ACC_X"].samples, [0.1, np.nan], equal_nan=True)
    np.testing.assert_allclose(s.channels["ENGINE_RPM"].samples, [np.nan, 2400], equal_nan=True)
    assert s.channels["ACC_X"].valid.tolist() == [True, False]
    assert s.channels["ENGINE_RPM"].valid.tolist() == [False, True]
    np.testing.assert_array_equal(s.channels["ENGINE_RPM"].time_s, s.elapsed_time_s)
    assert any(i.code == "missing_channel" and i.source == "LOG2.txt" for i in s.issues)


def test_boundary_anomalies_keep_rows_and_physical_source_lines(tmp_path):
    header = "NR;DATA;CZAS;GPS_LAT;GPS_LON;GPS_SPEED_KMH\n"
    (tmp_path / "LOG1.txt").write_text(header + "42;2026-01-01;00:00:01.000;0;0;36\n")
    (tmp_path / "LOG2.txt").write_text(
        header + "1;2026-01-01;00:00:01.000;0;0;36\n"
        "bad row\n2;2026-01-01;bad;91;0;36\n3;2026-01-01;00:00:05.000;0;0;36\n"
    )
    s = LegacyTxtImporter().load(tmp_path)
    assert s.nr.tolist() == [42, 1, 2, 3]
    assert np.all(np.diff(s.elapsed_time_s) > 0)
    assert s.elapsed_time_s[-1] == 4
    assert s.distance_m[-1] == pytest.approx(40)
    assert any(i.source == "LOG2.txt" and i.line == 2 and i.code == "nr_order" for i in s.issues)
    for code, line in [
        ("time_correction", 2),
        ("malformed_row", 3),
        ("invalid_time", 4),
        ("invalid_gps", 4),
        ("time_gap", 5),
    ]:
        assert any(i.source == "LOG2.txt" and i.line == line and i.code == code for i in s.issues)
    assert s.source_lines.tolist() == [2, 2, 4, 5]


def test_folder_exports_source_and_combined_axis(log_folder, tmp_path):
    s = LegacyTxtImporter().load(log_folder)
    csv_path, gpx_path = tmp_path / "out.csv", tmp_path / "out.gpx"
    export_csv(s, csv_path, ["ACC_X"])
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 301
    assert rows[110]["source_file"] == "LOG003.TxT"
    assert rows[110]["source_line"] == "2"
    assert float(rows[-1]["elapsed_time_s [s]"]) == 3
    assert float(rows[-1]["distance_m [m]"]) == pytest.approx(30)
    metadata = json.loads(csv_path.with_suffix(".csv.metadata.json").read_text())
    assert [f["name"] for f in metadata["source_files"]] == ["LOG2.txt", "LOG003.TxT", "LOG10.TXT"]
    export_gpx(s, gpx_path)
    points = ET.parse(gpx_path).findall(".//{*}trkpt")
    assert len(points) == 4
    assert points[-1].find(".//{*}source_file").text == "LOG10.TXT"


def test_combined_progress_and_cancellation(log_folder):
    progress = []
    LegacyTxtImporter().load(log_folder, progress.append)
    assert progress[0] == 0 and progress[-1] == 100
    assert progress == sorted(set(progress))

    def cancel(value):
        if value >= 40:
            raise InterruptedError("test cancellation")

    with pytest.raises(InterruptedError, match="test cancellation"):
        LegacyTxtImporter().load(log_folder, cancel)
