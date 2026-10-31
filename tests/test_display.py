import numpy as np
import pytest

from telemetry_viewer.domain.models import Channel
from telemetry_viewer.processing.display import elapsed_clock


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "00:00:00"),
        (59.999, "00:00:59"),
        (60, "00:01:00"),
        (3661, "01:01:01"),
        (90061, "25:01:01"),
        (-61, "-00:01:01"),
        (np.nan, "—"),
    ],
)
def test_elapsed_clock(value, expected):
    assert elapsed_clock(value) == expected


def test_cursor_clock_retains_milliseconds():
    assert elapsed_clock(3661.123, milliseconds=True) == "01:01:01.123"
    assert elapsed_clock(59.9999, milliseconds=True) == "00:01:00.000"


def test_unit_ranges_use_valid_measurements_and_handle_constant_or_empty_data():
    from telemetry_viewer.processing.scales import full_y_range

    # Calculation helper is Qt-free in behavior; no QApplication or display is needed.
    channel = Channel(
        "ACC_X",
        "g",
        "IMU",
        "Acceleration",
        np.array([-2.0, 1000, 3.0]),
        np.arange(3),
        np.array([True, False, True]),
    )
    assert full_y_range([channel]) == pytest.approx((-2.25, 3.25))
    channel.valid[:] = False
    assert full_y_range([channel]) == (-1, 1)
    channel.valid[:] = True
    channel.samples[:] = 0
    assert full_y_range([channel]) == (-0.01, 0.01)


def test_channels_with_unknown_units_have_separate_scales():
    from telemetry_viewer.processing.scales import channel_unit

    first = Channel("RPM", "", "CAN", "CAN", np.array([1000.0]), np.array([0.0]), np.array([True]))
    second = Channel(
        "THROTTLE", "", "CAN", "CAN", np.array([10.0]), np.array([0.0]), np.array([True])
    )
    assert channel_unit(first) != channel_unit(second)
    first.unit = second.unit = "percent"
    assert channel_unit(first) == channel_unit(second)
