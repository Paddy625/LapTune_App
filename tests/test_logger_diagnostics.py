import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from generate_navigation_demo import write_demo

from telemetry_viewer.exporters.files import export_csv
from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter


@pytest.fixture
def diagnostic_log(tmp_path):
    return write_demo(tmp_path / "LOG0010.TXT")


def test_current_36_column_schema_has_units_groups_and_preserved_values(diagnostic_log):
    before = hashlib.sha256(diagnostic_log.read_bytes()).digest()
    session = LegacyTxtImporter().load(diagnostic_log)
    with diagnostic_log.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    assert len(rows[0]) == 36
    assert len(session.channels) == 33  # Every column except NR/date/time is selectable.
    for name, group, unit in (
        ("T_ACC_US", "Timing", "µs"),
        ("T_GYRO_US", "Timing", "µs"),
        ("GPS_T_RX_US", "Timing", "µs"),
        ("GPS_T_MEAS_US", "Timing", "µs"),
        ("TIME_SYNC_STATE", "Timing", "code"),
        ("GPS_STATE", "GPS status", "code"),
        ("GPS_FIX_ID", "GPS status", "count"),
        ("GPS_ITOW_MS", "GPS status", "ms"),
        ("IMU_VALID", "Logger diagnostics", "flag"),
        ("IMU_FLAGS", "Logger diagnostics", "bitmask"),
        ("SD_MAX_PAUSE_US", "Logger diagnostics", "µs"),
    ):
        channel = session.channels[name]
        assert (channel.group, channel.unit, channel.source) == (group, unit, "Logger")
        assert channel.description
    assert not any(c.group == "CAN / Other" for c in session.channels.values())
    for name, channel in session.channels.items():
        if name == "GPS_STATE":
            continue
        expected = np.array([float(r[name]) if r[name] else np.nan for r in rows])
        np.testing.assert_array_equal(channel.samples, expected)
    state = session.channels["GPS_STATE"]
    assert state.samples[:3].tolist() == [0, 0, 0]
    assert state.samples[100] == 1 and state.samples[101] == 2
    assert state.value_labels == {0: "I", 1: "N", 2: "R"}
    assert state.interpolation == "hold"
    assert session.logger_fields["GPS_STATE"] == [r["GPS_STATE"] for r in rows]
    assert before == hashlib.sha256(diagnostic_log.read_bytes()).digest()


def test_state_and_diagnostics_export_numeric_values_and_text_legend(diagnostic_log, tmp_path):
    session = LegacyTxtImporter().load(diagnostic_log)
    path = tmp_path / "diagnostics.csv"
    export_csv(session, path, ["GPS_STATE", "TIME_SYNC_STATE", "GPS_T_MEAS_US"])
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[100]["GPS_STATE"] == "N" and rows[100]["GPS_STATE [code]"] == "1.0"
    assert rows[101]["GPS_STATE"] == "R" and rows[101]["GPS_STATE [code]"] == "2.0"
    metadata = json.loads(path.with_suffix(".csv.metadata.json").read_text(encoding="utf-8"))
    assert metadata["channels"]["GPS_STATE"]["value_labels"] == {"0": "I", "1": "N", "2": "R"}
    assert metadata["channels"]["GPS_T_MEAS_US"]["unit"] == "µs"


def test_mixed_folder_keeps_missing_diagnostics_invalid(log_file, diagnostic_log, tmp_path):
    folder = tmp_path / "mixed"
    folder.mkdir()
    (folder / "LOG1.TXT").write_bytes(log_file.read_bytes())
    (folder / "LOG2.TXT").write_bytes(diagnostic_log.read_bytes())
    session = LegacyTxtImporter().load(folder)
    assert not session.channels["GPS_STATE"].valid[:301].any()
    assert not session.channels["GPS_T_MEAS_US"].valid[:301].any()
    assert session.logger_fields["GPS_STATE"][:301] == [""] * 301
    assert not any(
        issue.code == "missing_channel" and "IMU_VALID" in issue.message for issue in session.issues
    )


def test_future_diagnostics_and_invalid_text_state_keep_their_meaning(tmp_path):
    path = tmp_path / "future.TXT"
    path.write_text(
        "NR;DATA;CZAS;GPS_STATE;PPS_ERROR_US;IMU_NEW_ERRORS;ENGINE_RPM\n"
        "1;2026-01-01;00:00:00.000;N;12;0;1000\n"
        "2;2026-01-01;00:00:00.010;?;13;1;1001\n"
        "3;2026-01-01;00:00:00.020;;14;1;1002\n",
        encoding="utf-8",
    )
    session = LegacyTxtImporter().load(path)
    assert session.channels["GPS_STATE"].valid.tolist() == [True, False, False]
    assert session.logger_fields["GPS_STATE"] == ["N", "?", ""]
    assert session.channels["PPS_ERROR_US"].unit == "µs"
    assert session.channels["IMU_NEW_ERRORS"].group == "Logger diagnostics"
    assert session.channels["ENGINE_RPM"].group == "CAN / Other"
    assert sum(i.code == "invalid_gps_state" for i in session.issues) == 1
