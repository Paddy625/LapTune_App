import math
from dataclasses import replace

import numpy as np
import pytest

from telemetry_viewer.domain.models import Channel
from telemetry_viewer.processing.signals import SignalRecipe, calculate_signal, channel_statistics


def channel(values, times=None, unit="g", name="ACC_X"):
    values = np.asarray(values, dtype=float)
    times = np.asarray(times if times is not None else np.arange(len(values)) * 0.01, dtype=float)
    return Channel(name, unit, "test", "Acceleration", values, times, np.isfinite(values))


def calculate(source, operation, **kwargs):
    return calculate_signal(
        {source.name: source}, SignalRecipe("CALC_TEST", operation, (source.name,), **kwargs)
    )


def test_lowpass_attenuates_noise_preserves_load_and_does_not_mutate_source():
    times = np.cumsum(np.resize([0.003, 0.007, 0.005], 12000))
    load = 0.7 * np.sin(2 * math.pi * times)
    noise = 0.3 * np.sin(2 * math.pi * 35 * times)
    source = channel(1 + load + noise, times)
    before = source.samples.copy()
    result = calculate(source, "lowpass", cutoff_hz=5)
    interior = (times > 2) & (times < times[-1] - 2)
    assert np.std((result.samples - 1 - load)[interior]) < 0.04
    assert np.std((source.samples - 1 - load)[interior]) > 0.2
    np.testing.assert_array_equal(source.samples, before)
    assert result.time_s is source.time_s
    assert result.group == "Calculated" and result.calculation["cutoff_hz"] == 5


def test_causal_filter_uses_actual_uneven_time_intervals():
    times = np.array([0, 0.001, 0.01, 0.03, 0.031, 0.07])
    result = calculate(channel([0, 1, 1, 1, 1, 1], times), "lowpass", cutoff_hz=2, zero_phase=False)
    np.testing.assert_allclose(result.samples, 1 - np.exp(-2 * np.pi * 2 * times))


def test_filters_reset_across_invalid_samples_and_long_gaps():
    source = channel([1, 1, np.nan, 7, 7, 12, 12], [0, 0.01, 0.02, 0.03, 0.04, 4, 4.01])
    result = calculate(source, "lowpass")
    np.testing.assert_allclose(result.samples, source.samples, equal_nan=True)
    np.testing.assert_array_equal(result.valid, source.valid)
    high = calculate(source, "highpass")
    np.testing.assert_allclose(high.samples[high.valid], 0)


@pytest.mark.parametrize(
    ("source_unit", "target_unit", "expected"),
    [
        ("g (assumed)", "m/s²", 9.80665),
        ("km/h", "m/s", 1 / 3.6),
        ("deg/s", "rad/s", math.pi / 180),
    ],
)
def test_unit_conversion(source_unit, target_unit, expected):
    result = calculate(channel([1, 2], unit=source_unit), "convert", unit=target_unit)
    np.testing.assert_allclose(result.samples, [expected, expected * 2])
    assert result.unit == target_unit


def test_gain_offset_and_incompatible_units():
    np.testing.assert_allclose(
        calculate(channel([1, 2]), "scale", gain=3, offset=-1).samples, [2, 5]
    )
    with pytest.raises(ValueError, match="physical quantity"):
        calculate(channel([1, 2]), "convert", unit="km/h")


def test_vector_magnitude_aligns_only_exact_shared_measurements():
    a = channel([3, 30, 3], [0, 0.1, 0.2], name="A")
    b = channel([4, 40, 4], [0, 0.15, 0.2], name="B")
    result = calculate_signal({"A": a, "B": b}, SignalRecipe("MAG", "magnitude", ("A", "B")))
    np.testing.assert_array_equal(result.time_s, [0, 0.2])
    np.testing.assert_allclose(result.samples, [5, 5])
    difference = calculate_signal({"A": a, "B": b}, SignalRecipe("DIFF", "difference", ("A", "B")))
    np.testing.assert_allclose(difference.samples, [-1, -1])


def test_derivative_and_integral_use_actual_dt_and_reset_at_gaps():
    source = channel([0, 1, 3, 4], [0, 0.1, 0.3, 0.4], unit="m/s")
    np.testing.assert_allclose(calculate(source, "derivative").samples, 10)
    np.testing.assert_allclose(calculate(source, "integral").samples, [0, 0.05, 0.45, 0.8])
    source.time_s = np.array([0, 0.1, 5, 5.1])
    np.testing.assert_allclose(calculate(source, "integral").samples, [0, 0.05, 0, 0.35])


def test_invalid_recipe_and_cancellation_do_not_publish_a_channel():
    source = channel(np.ones(20000))
    recipe = SignalRecipe("CALC", "lowpass", ("ACC_X",))
    for invalid in [
        replace(recipe, name="ACC_X"),
        replace(recipe, cutoff_hz=0),
        replace(recipe, max_gap_s=0),
    ]:
        with pytest.raises(ValueError):
            calculate_signal({source.name: source}, invalid)

    def cancel():
        raise InterruptedError("cancelled")

    with pytest.raises(InterruptedError):
        calculate_signal({source.name: source}, recipe, cancel)


def test_statistics_use_valid_full_resolution_samples_in_visible_range():
    source = channel([0, np.nan, 500, 1, -10], [0, 0.1, 0.2, 0.3, 0.4])
    stats = channel_statistics(source, source.time_s, (0.1, 0.3))
    assert stats["count"] == 2 and stats["max"] == 500 and stats["mean"] == 250.5
    assert channel_statistics(source, source.time_s, (2, 3))["count"] == 0


def test_calculated_csv_export_retains_processing_parameters(log_file, tmp_path):
    import csv
    import json

    from telemetry_viewer.exporters.files import export_csv
    from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter

    session = LegacyTxtImporter().load(log_file)
    recipe = SignalRecipe("CALC_ACC", "lowpass", ("ACC_X",), cutoff_hz=3)
    session.channels[recipe.name] = calculate_signal(session.channels, recipe)
    target = tmp_path / "calculated.csv"
    export_csv(session, target, [recipe.name])
    with target.open() as handle:
        rows = list(csv.reader(handle))
    assert rows[0][-1] == "CALC_ACC [g (assumed)]"
    assert len(rows) == len(session.nr) + 1
    metadata = json.loads(target.with_suffix(".csv.metadata.json").read_text())
    parameters = metadata["channels"][recipe.name]["calculation"]
    assert parameters["cutoff_hz"] == 3 and parameters["sources"] == ["ACC_X"]
