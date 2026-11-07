import numpy as np
import pytest

from telemetry_viewer.processing.load_filter import smooth_loads


def test_constant_load_and_zero_gravity_component_are_preserved():
    time = np.arange(1001) / 100
    loads = np.tile([-0.2, 0.3, 0.0], (len(time), 1))
    before = loads.copy()
    result, report = smooth_loads(time, loads, np.zeros(len(time), dtype=bool))
    valid = np.isfinite(result).all(axis=1)
    np.testing.assert_allclose(result[valid], loads[valid], atol=1e-12)
    np.testing.assert_array_equal(loads, before)
    assert not valid[:60].any() and not valid[-60:].any()
    assert report["filtered_runs"] == 1


@pytest.mark.parametrize("cutoff", [2.0, 5.0, 10.0])
def test_requested_combined_cutoff_and_zero_phase(cutoff):
    time = np.arange(4001) / 100
    loads = np.column_stack(
        [np.sin(2 * np.pi * cutoff * time), np.sin(2 * np.pi * time), np.sin(80 * np.pi * time)]
    )
    result, _ = smooth_loads(time, loads, np.zeros(len(time), dtype=bool), cutoff)
    interior = (time > 5) & (time < 35)
    for axis, frequency, amplitude, tolerance in (
        (0, cutoff, 1 / np.sqrt(2), 0.002),
        (1, 1, 1.0, 0.003),
        (2, 40, 0.0, 0.001),
    ):
        phase = 2 * np.pi * frequency * time[interior]
        design = np.column_stack([np.sin(phase), np.cos(phase), np.ones(len(phase))])
        fitted = np.linalg.lstsq(design, result[interior, axis], rcond=None)[0]
        assert fitted[0] == pytest.approx(amplitude, abs=tolerance)
        assert fitted[1] == pytest.approx(0, abs=0.001)


def test_nonuniform_imu_times_keep_signal_alignment():
    time = np.arange(2001) / 100
    time += 0.0001 * np.sin(np.arange(len(time)) * 0.7)
    loads = np.column_stack([np.sin(2 * np.pi * time)] * 3)
    result, _ = smooth_loads(time, loads, np.zeros(len(time), dtype=bool))
    interior = (time > 2) & (time < 18)
    np.testing.assert_allclose(result[interior], loads[interior], atol=0.002)


@pytest.mark.parametrize("boundary", ["missing", "gap", "restart"])
def test_filter_never_crosses_invalid_imu_gaps_or_navigation_restarts(boundary):
    time = np.arange(1001) / 100
    loads = np.tile([1.0, 2.0, 3.0], (len(time), 1))
    loads[500:] = [-10, -20, -30]
    starts = np.zeros(len(time), dtype=bool)
    if boundary == "missing":
        loads[499:501] = np.nan
    elif boundary == "gap":
        time[500:] += 2
    else:
        starts[500] = True
    result, report = smooth_loads(time, loads, starts)
    valid = np.isfinite(result).all(axis=1)
    np.testing.assert_allclose(result[valid], loads[valid], atol=1e-10)
    assert not valid[450:550].any()
    assert report["filtered_runs"] == 2


def test_short_and_undersampled_runs_are_explicitly_unavailable():
    for time, field in (
        (np.arange(10) / 100, "short_runs_skipped"),
        (np.arange(101) / 5, "undersampled_runs_skipped"),
    ):
        result, report = smooth_loads(
            time, np.ones((len(time), 3)), np.zeros(len(time), dtype=bool)
        )
        assert np.isnan(result).all() and report[field] == 1


def test_smoothing_cancellation_propagates_without_changing_inputs():
    time = np.arange(1001) / 100
    loads = np.ones((len(time), 3))

    def cancel():
        raise InterruptedError("cancel smoothing")

    with pytest.raises(InterruptedError, match="cancel smoothing"):
        smooth_loads(time, loads, np.zeros(len(time), dtype=bool), check=cancel)
    assert (loads == 1).all()
