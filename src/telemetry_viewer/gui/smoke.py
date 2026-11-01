"""Same end-to-end self-check runs in source CI and the Windows frozen executable."""

import json
import time
import traceback
from pathlib import Path

import numpy as np
from PySide6.QtCore import QTimer


def run_smoke(app, window, path: Path, result_path: Path | None = None):
    started = time.monotonic()
    state = {"phase": "loading"}
    window.layer_combo.setCurrentText("Offline")
    window.load_file(path)
    timer = QTimer(window)

    def fail(message):
        message = f"{state['phase']}: {message}"
        if result_path:
            result_path.write_text(json.dumps({"passed": False, "error": message}))
        print("GUI_SMOKE_FAIL " + message, flush=True)
        timer.stop()
        window.close()
        app.exit(1)

    def map_click_received(_lat, _lon):
        if state["phase"] == "waiting_map_click":
            # MainWindow's earlier connection has already applied the cursor change.
            if window.cursor_sample != 0:
                fail(f"Map click selected unexpected row {window.cursor_sample}")
                return
            state["phase"] = "map_clicked"

    window.map_widget.location_selected.connect(map_click_received)

    def verify_js(value):
        try:
            value = json.loads(value)
            assert value["ready"] and value["routeLayers"] > 0
            assert value["markerRow"] == window.session.fix_for_sample(window.cursor_sample)
            assert value["notice"].startswith("Offline")
            state["phase"] = "waiting_map_click"
            window.map_widget.page.runJavaScript(
                # Exercise delayed WebChannel delivery on both source and frozen builds.
                # The synthetic fixture starts at the equator/prime meridian.
                "setTimeout(()=>bridge.locationClicked(0,0),400)"
            )
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
                assert not window.brand_logo.pixmap().isNull()
                assert window.open_folder_action.shortcut().toString() == "Ctrl+Shift+O"
                for source in s.source_files:
                    assert window.is_source_file(source.path)
                assert not window.is_source_file(path.parent / "export-check.csv")
                window.axis_combo.setCurrentText("Distance")
                assert window.plots.mode == "distance"
                window.set_cursor(len(s.nr) // 2)
                assert (
                    s.source_for_sample(window.cursor_sample).path.name
                    in window.cursor_label.text()
                )
                window.axis_combo.setCurrentText("Time")
                assert window.plots.mode == "time"
                assert len(
                    next(iter(window.plots.curves.values()))[0].getOriginalDataset()[0]
                ) == len(s.nr)
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
                        json.dumps(
                            {
                                "passed": True,
                                "samples": len(window.session.nr),
                                "source_files": len(window.session.source_files),
                                "source_names": [s.path.name for s in window.session.source_files],
                            }
                        )
                    )
                timer.stop()
                window.close()
                app.exit(0)
        except Exception:
            fail(traceback.format_exc())

    timer.timeout.connect(step)
    timer.start(150)


def run_navigation_smoke(app, window, path: Path, result_path: Path | None = None):
    """Exercise the actual packaged ESKF/numpy code and EST Leaflet layer, not just imports."""
    from telemetry_viewer.processing.navigation import NavigationConfig

    started = time.monotonic()
    state = {"phase": "loading"}
    timer = QTimer(window)

    def finish(passed, detail=""):
        data = {"passed": passed, "detail": detail}
        if passed:
            data["estimated_channels"] = len(window.session.navigation.channels)
        if result_path:
            result_path.write_text(json.dumps(data))
        print(
            "GUI_NAVIGATION_SMOKE_" + ("PASS" if passed else "FAIL") + " " + json.dumps(data),
            flush=True,
        )
        timer.stop()
        window.close()
        app.exit(0 if passed else 1)

    def fail(message):
        finish(False, state["phase"] + ": " + str(message))

    def verify_map(value):
        try:
            data = json.loads(value)
            assert data["layers"] > 0 and data["row"] == state["row"]
            finish(True)
        except Exception:
            fail(traceback.format_exc())

    def step():
        try:
            if time.monotonic() - started > 40:
                raise TimeoutError("Waiting for EST computation or map")
            if state["phase"] == "loading":
                if (
                    window.session is None
                    or window.thread is not None
                    or not window.map_widget.is_ready
                ):
                    return
                state["raw"] = {
                    name: channel.samples.copy()
                    for name, channel in window.session.channels.items()
                }
                state["phase"] = "estimating"
                window.start_navigation(NavigationConfig())
            elif state["phase"] == "estimating":
                session = window.session
                if session.navigation is None or window.calculation_thread is not None:
                    return
                for name, values in state["raw"].items():
                    np.testing.assert_array_equal(session.channels[name].samples, values)
                result = session.navigation
                assert all(name.startswith("EST_") for name in result.channels)
                first_gps = np.flatnonzero(session.channels["GPS_LAT"].valid)[0]
                assert not result.channels["EST_LAT"].valid[:first_gps].any()
                outage = np.flatnonzero(
                    result.channels["EST_LAT"].valid & ~session.channels["GPS_LAT"].valid
                )
                assert len(outage), "Fixture must include an initialized GPS loss"
                state["row"] = int(outage[len(outage) // 2])
                window.set_cursor(state["row"])
                window.map_widget.set_track_mode("EST")
                state["phase"] = "map"
                window.map_widget.page.runJavaScript(
                    "JSON.stringify({layers:estimatedRoute.getLayers().length,row:estimatedRow})",
                    verify_map,
                )
        except Exception:
            fail(traceback.format_exc())

    window.import_error = fail
    window.calculation_error = fail
    window.layer_combo.setCurrentText("Offline")
    window.load_file(path)
    timer.timeout.connect(step)
    timer.start(100)
