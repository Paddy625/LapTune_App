"""Same end-to-end self-check runs in source CI and the Windows frozen executable."""

import json
import time
from pathlib import Path

from PySide6.QtCore import QTimer


def run_smoke(app, window, path: Path, result_path: Path | None = None):
    started = time.monotonic()
    state = {"phase": "loading"}
    window.layer_combo.setCurrentText("Offline")
    window.load_file(path)
    timer = QTimer(window)

    def fail(message):
        if result_path:
            result_path.write_text(json.dumps({"passed": False, "error": message}))
        print("GUI_SMOKE_FAIL " + message, flush=True)
        timer.stop()
        window.close()
        app.exit(1)

    def verify_js(value):
        try:
            value = json.loads(value)
            assert value["ready"] and value["routeLayers"] > 0
            assert value["markerRow"] == window.session.fix_for_sample(window.cursor_sample)
            assert value["notice"].startswith("Offline")
            window.map_widget.page.runJavaScript(
                "bridge.locationClicked(0,0)"  # synthetic fixture starts at equator/prime meridian
            )
            state["phase"] = "map_clicked"
        except Exception as exc:
            fail(f"Map bridge/render check: {exc}: {value}")

    def step():
        try:
            if time.monotonic() - started > 40:
                fail("Timed out waiting for local import/WebEngine")
                return
            if state["phase"] == "loading":
                if (
                    window.session is None
                    or window.thread is not None
                    or not window.map_widget.is_ready
                ):
                    return
                s = window.session
                assert len(s.nr) > 1 and len(s.gps_fix_samples) > 1
                assert window.plots.curves
                window.axis_combo.setCurrentText("Distance")
                assert window.plots.mode == "distance"
                window.set_cursor(len(s.nr) // 2)
                window.axis_combo.setCurrentText("Time")
                assert window.plots.mode == "time"
                assert len(next(iter(window.plots.curves.values()))[0].getData()[0]) == len(s.nr)
                window.plots.fit_all()
                state["phase"] = "checking_js"
                window.map_widget.page.runJavaScript(
                    "JSON.stringify({ready:ready, routeLayers:route.getLayers().length,"
                    "markerRow:markerRow,"
                    "notice:notice.textContent})",
                    verify_js,
                )
            elif state["phase"] == "map_clicked":
                assert window.cursor_sample == 0
                if window.thread is not None:
                    return
                print(
                    "GUI_SMOKE_PASS "
                    + json.dumps(
                        {
                            "samples": len(window.session.nr),
                            "map": "local Leaflet offline",
                            "axes": ["time", "distance"],
                            "synchronization": "both directions",
                        }
                    ),
                    flush=True,
                )
                if result_path:
                    result_path.write_text(
                        json.dumps({"passed": True, "samples": len(window.session.nr)})
                    )
                timer.stop()
                window.close()
                app.exit(0)
        except Exception as exc:
            fail(repr(exc))

    timer.timeout.connect(step)
    timer.start(150)
