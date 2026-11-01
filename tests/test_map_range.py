import numpy as np

from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter
from telemetry_viewer.processing.map_range import RouteRange


def test_range_snaps_to_temporal_fixes_and_keeps_tangent_inside_segments(log_file):
    session = LegacyTxtImporter().load(log_file)
    locator = RouteRange(session)
    marks = locator.locate(0.4, 2.7, "time")
    assert marks["start"]["row"] == 0
    assert marks["end"]["row"] == 300
    assert locator.locate(30, 40, "time") is None
    session.gps_segment_start[2] = True
    locator = RouteRange(session)
    assert locator.locate(1.3, 1.7, "time") == {"start": None, "end": None}
    point = locator.marker(1)
    assert point["after"] == point["point"]


def test_distance_plateaus_use_first_and_last_recorded_instant(log_file):
    session = LegacyTxtImporter().load(log_file)
    session.distance_speed_m[:] = 0
    result = RouteRange(session).locate(0, 0, "distance")
    assert result["start"]["row"] == 0
    assert result["end"]["row"] == 300


def test_no_gps_and_single_stationary_fix(log_file):
    session = LegacyTxtImporter().load(log_file)
    session.gps_fix_samples = np.array([0])
    session.gps_segment_start = np.array([True])
    marker = RouteRange(session).locate(-1, 10, "time")["start"]
    assert marker["before"] == marker["after"] == marker["point"]
    session.gps_fix_samples = np.array([], dtype=int)
    session.gps_segment_start = np.array([], dtype=bool)
    assert RouteRange(session).locate(0, 1, "time") is None
