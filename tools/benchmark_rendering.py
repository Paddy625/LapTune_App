"""Synthetic multi-file session benchmark including actual Qt paint and map round trips."""

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QTimer

from telemetry_viewer.app import create_application, isolated_settings
from telemetry_viewer.domain.models import Channel, Session, SourceFile
from telemetry_viewer.gui.main_window import MainWindow


def synthetic_session(count):
    times = np.arange(count, dtype=np.float64) / 100
    fix_rows = np.arange(0, count, 20, dtype=np.int64)
    gps_time = np.floor(times)
    values = {
        "ACC_X": (np.sin(times * 3) * 0.3, "g", "Acceleration"),
        "ACC_Y": (np.cos(times * 2) * 0.3, "g", "Acceleration"),
        "ACC_Z": (1 + np.sin(times * 5) * 0.05, "g", "Acceleration"),
        "GYRO_X": (np.sin(times) * 2, "deg/s", "Gyroscope"),
        "GYRO_Y": (np.cos(times) * 3, "deg/s", "Gyroscope"),
        "GYRO_Z": (np.sin(times / 4) * 12, "deg/s", "Gyroscope"),
        "GPS_LAT": (np.sin(gps_time / 35) * 0.003, "deg", "GPS"),
        "GPS_LON": (np.cos(gps_time / 35) * 0.004, "deg", "GPS"),
        "GPS_SPEED_KMH": (42 + np.sin(np.floor(times * 5) / 60) * 14, "km/h", "GPS"),
    }
    channels = {
        n: Channel(n, unit, "Synthetic", group, y, times, np.ones(count, dtype=bool))
        for n, (y, unit, group) in values.items()
    }
    sources = [
        SourceFile(Path(f"LOG{i + 1}.TXT").resolve(), start, min(start + 18640, count))
        for i, start in enumerate(range(0, count, 18640))
    ]
    return Session(
        "Synthetic benchmark",
        np.arange(count),
        np.arange(count),
        [],
        times,
        channels,
        fix_rows,
        np.arange(len(fix_rows)) == 0,
        times * 12,
        times * 12,
        source_files=sources,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=186400)
    parser.add_argument("--frames", type=int, default=90)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--mixed", action="store_true", help="Mix acceleration, gyro and speed on plot 1"
    )
    args = parser.parse_args()
    preferences = isolated_settings()
    app = create_application([])
    window = MainWindow()
    window.layer_combo.setCurrentText("Offline")
    window.resize(1560, 920)
    window.show()
    session = synthetic_session(args.samples)
    cursor, painted, map_latency = [], [], []
    state = {"frames": 0}
    initial = time.perf_counter()
    window.pending_path = Path("synthetic-benchmark").resolve()
    window.accept_session(session)
    if args.mixed:
        window.channels.apply_plot_selection(0, ["ACC_X", "GYRO_Z", "GPS_SPEED_KMH"])
    load_seconds = time.perf_counter() - initial
    expected_curves = [id(c) for c, _ in window.plots.curves.values()]
    timer = QTimer(window)

    def finish():
        result = {
            "samples": args.samples,
            "files": len(session.source_files),
            "frames": len(cursor),
            "mixed_units": args.mixed,
            "gui_setup_s": round(load_seconds, 3),
            "cursor_p95_ms": round(float(np.percentile(cursor, 95)), 3),
            "paint_p95_ms": round(float(np.percentile(painted, 95)), 3),
            "steady_paint_p95_ms": round(
                float(np.percentile([v for i, v in enumerate(painted) if i % 15 != 0], 95)), 3
            ),
            "map_roundtrip_p95_ms": round(float(np.percentile(map_latency, 95)), 3),
            "display_points": {
                f"{n[0] + 1}:{n[1]}": len(c.getData()[0])
                for n, (c, _) in window.plots.curves.items()
            },
            "curves_reused": expected_curves == [id(c) for c, _ in window.plots.curves.values()],
        }
        print(json.dumps(result), flush=True)
        if args.output:
            args.output.write_text(json.dumps(result, indent=2))
        window.close()
        app.quit()

    def map_done(value):
        map_latency.append(1000 * (time.perf_counter() - state["map_start"]))
        if state["frames"] >= args.frames:
            finish()
        else:
            timer.start(16)

    def tick():
        if not window.map_widget.is_ready:
            timer.start(50)
            return
        frame = state["frames"]
        row = (frame * 1667) % len(session.nr)
        before = time.perf_counter()
        window.set_cursor(row)
        cursor.append(1000 * (time.perf_counter() - before))
        if frame % 15 == 0:
            span = min(session.elapsed_time_s[-1], 300)
            offset = min(session.elapsed_time_s[row], max(0, session.elapsed_time_s[-1] - span))
            window.plots.plots[0].setXRange(float(offset), float(offset + span), padding=0)
        before = time.perf_counter()
        window.plots.graphics.viewport().repaint()
        painted.append(1000 * (time.perf_counter() - before))
        state["frames"] += 1
        state["map_start"] = time.perf_counter()
        # Includes asynchronous WebEngine work queued by set_cursor, not just Python timing.
        window.map_widget.page.runJavaScript("markerRow", map_done)

    timer.setSingleShot(True)
    timer.timeout.connect(tick)
    timer.start(300)
    QTimer.singleShot(60000, lambda: app.exit(2))
    result = app.exec()
    window.settings.sync()
    preferences.cleanup()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
