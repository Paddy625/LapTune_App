"""Measure real import and cursor updates, without outputting private track data."""

import json
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QTimer

from telemetry_viewer.app import create_application, isolated_settings
from telemetry_viewer.gui.main_window import MainWindow

preferences = isolated_settings()
app = create_application([])
window = MainWindow()
window.layer_combo.setCurrentText("Offline")
window.show()
started = time.perf_counter()
intervals, cursor_ms = [], []
state = {"last": started, "import_seconds": None, "initial_curves": None}
timer = QTimer(window)


def tick():
    now = time.perf_counter()
    if window.session is None or window.thread is not None:
        intervals.append(now - state["last"])
    elif window.map_widget.is_ready:
        if state["import_seconds"] is None:
            state["import_seconds"] = now - started
            state["initial_curves"] = [id(c) for c, _ in window.plots.curves.values()]
        before = time.perf_counter()
        window.set_cursor((len(cursor_ms) * 67) % len(window.session.nr))
        cursor_ms.append(1000 * (time.perf_counter() - before))
        assert state["initial_curves"] == [id(c) for c, _ in window.plots.curves.values()]
        if len(cursor_ms) == 300:
            print(
                json.dumps(
                    {
                        "rows": len(window.session.nr),
                        "import_and_render_seconds": round(state["import_seconds"], 3),
                        "import_max_ui_tick_gap_ms": round(1000 * max(intervals, default=0), 3),
                        "cursor_p95_ms": round(float(np.percentile(cursor_ms, 95)), 3),
                        "cursor_max_ms": round(max(cursor_ms), 3),
                        "curve_objects_preserved": True,
                    }
                ),
                flush=True,
            )
            window.close()
            app.quit()
    state["last"] = now
    if now - started > 40:
        print("Benchmark timed out", flush=True)
        window.close()
        app.exit(1)


timer.timeout.connect(tick)
timer.start(10)
window.load_file(Path(sys.argv[1]))
raise SystemExit(app.exec())
