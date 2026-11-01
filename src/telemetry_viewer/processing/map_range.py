"""Snap viewport bounds to accepted GPS fixes without joining gaps or repeated passes."""

import numpy as np

from telemetry_viewer.processing.display import elapsed_clock


class RouteRange:
    def __init__(self, session):
        self.session = session
        self.rows = session.gps_fix_samples
        self.axes = {}
        self.points = np.column_stack(
            (
                session.channels["GPS_LAT"].samples[self.rows],
                session.channels["GPS_LON"].samples[self.rows],
            )
        )
        starts = session.gps_segment_start.copy()
        if len(starts):
            starts[0] = True
        self.segments = np.cumsum(starts)
        changed = (
            np.r_[True, np.any(np.diff(self.points, axis=0) != 0, axis=1)]
            if len(self.rows)
            else np.array([], dtype=bool)
        )
        vertices = changed | starts
        self.vertex_indices = np.flatnonzero(vertices)
        self.vertex_ids = np.cumsum(vertices) - 1

    def marker(self, index):
        vertex = self.vertex_ids[index]
        neighbor_indices = []
        for candidate in (max(0, vertex - 1), min(len(self.vertex_indices) - 1, vertex + 1)):
            neighbor = self.vertex_indices[candidate]
            neighbor_indices.append(
                neighbor if self.segments[neighbor] == self.segments[index] else index
            )
        before, after = neighbor_indices
        if np.array_equal(self.points[before], self.points[after]):
            # At a reversal A -> B -> A the centered chord is zero despite motion.
            # Prefer the outgoing leg, then the incoming one, within this segment.
            if not np.array_equal(self.points[index], self.points[after]):
                before = index
            elif not np.array_equal(self.points[before], self.points[index]):
                after = index
        row = int(self.rows[index])
        return {
            "point": self.points[index].tolist(),
            "before": self.points[before].tolist(),
            "after": self.points[after].tolist(),
            "row": row,
            "time": elapsed_clock(float(self.session.elapsed_time_s[row]), milliseconds=True),
        }

    def locate(self, lo, hi, mode):
        if not len(self.rows) or not np.isfinite([lo, hi]).all() or hi < lo:
            return None
        key = (mode, self.session.distance_method)
        if key not in self.axes:
            self.axes[key] = self.session.axis(mode)[self.rows]
        values = self.axes[key]
        if hi < values[0] or lo > values[-1]:
            return None

        def endpoint(value, side):
            i = int(np.searchsorted(values, value, side=side))
            if side == "right" and i and values[i - 1] == value:
                i -= 1  # Last recorded instant on a distance plateau.
            elif 0 < i < len(values) and values[i] != value:
                if self.segments[i] != self.segments[i - 1]:
                    return None  # No recorded route across a GPS gap.
                if value - values[i - 1] <= values[i] - value:
                    i -= 1
            return self.marker(int(np.clip(i, 0, len(values) - 1)))

        return {"start": endpoint(lo, "left"), "end": endpoint(hi, "right")}
