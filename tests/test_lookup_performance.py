import numpy as np

from telemetry_viewer.domain.models import SourceFile
from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter


class NoArrayGather(np.ndarray):
    """Cursor lookup must not allocate a copy of the whole GPS timeline."""

    def __getitem__(self, key):
        assert not isinstance(key, np.ndarray), "GPS lookup copied an entire time array"
        return super().__getitem__(key)


def test_gps_lookup_is_exact_without_gathering_full_timeline(log_file):
    session = LegacyTxtImporter().load(log_file)
    # Irregular positive dt tests nearest-time selection rather than nearest row index.
    time = np.cumsum(np.random.default_rng(7).uniform(0.001, 0.1, len(session.nr)))
    session.elapsed_time_s = time.view(NoArrayGather)
    session.gps_fix_samples = np.array([0, 13, 81, 190, 300])
    for sample in range(len(time)):
        expected = session.gps_fix_samples[
            np.argmin(abs(time[session.gps_fix_samples] - time[sample]))
        ]
        assert session.fix_for_sample(sample) == expected


def test_shared_distance_axis_reuses_source_array_and_independent_channels_still_map(log_file):
    session = LegacyTxtImporter().load(log_file)
    channel = session.channels["ACC_X"]
    assert session.channel_axis(channel, "distance") is session.distance_m
    channel.time_s = np.array([0.0, 0.25, 1.5])
    np.testing.assert_allclose(session.channel_axis(channel, "distance"), [0, 2.5, 15])


class CountedSources(list):
    reads = 0

    def __getitem__(self, index):
        self.reads += 1
        return super().__getitem__(index)

    def __iter__(self):
        raise AssertionError("Cursor lookup scanned all source files")


def test_source_lookup_handles_many_files_without_linear_scan(log_file):
    session = LegacyTxtImporter().load(log_file)
    template = session.source_files[0].path
    session.source_files = CountedSources(
        SourceFile(template, i * 10, (i + 1) * 10) for i in range(10000)
    )
    source = session.source_for_sample(99999)
    assert source.start_row == 99990
    assert session.source_files.reads < 25
    assert session.source_for_sample(-1) is None
    assert session.source_for_sample(100000) is None
