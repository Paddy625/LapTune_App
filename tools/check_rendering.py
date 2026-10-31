"""GUI regression checks for real downsampling, branding and isolated map rendering."""

import json
from pathlib import Path

import numpy as np
from benchmark_rendering import synthetic_session
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QToolBar

from telemetry_viewer.app import create_application, isolated_settings
from telemetry_viewer.gui.main_window import MainWindow

preferences = isolated_settings()
app = create_application([])
window = MainWindow()
window.layer_combo.setCurrentText("Offline")
window.resize(1560, 920)
window.show()
session = synthetic_session(186400)
# Narrow spikes must survive display reduction; actual measurement arrays remain unchanged.
session.channels["ACC_X"].samples[50003] = 500
session.channels["ACC_X"].samples[61007] = -400
window.pending_path = Path("synthetic-render-check").resolve()
window.accept_session(session)
state = {"phase": "waiting", "baseline": None}
timer = QTimer(window)


def fail(message):
    print("RENDER_CHECK_FAIL " + message, flush=True)
    timer.stop()
    window.close()
    app.exit(1)


def js_result(value):
    try:
        result = json.loads(value)
        assert result["separate"]
        assert result["vertices"] == len(
            np.unique(np.floor(session.elapsed_time_s[session.gps_fix_samples]))
        )
        assert result["redraws"] == 0, "Moving marker repainted the static route"
        print("RENDER_CHECK_PASS " + json.dumps(result), flush=True)
        timer.stop()
        window.close()
        app.quit()
    except Exception as exc:
        fail(repr(exc))


def check():
    try:
        if not window.map_widget.is_ready:
            return
        if state["phase"] == "waiting":
            toolbar = window.findChild(QToolBar, "analysis")
            actions = toolbar.actions()
            assert actions.index(window.open_folder_action) < actions.index(window.open_action)
            file_actions = window.menuBar().actions()[0].menu().actions()
            assert file_actions.index(window.open_folder_action) < file_actions.index(
                window.open_action
            )
            assert not window.brand_logo.pixmap().isNull()
            assert window.brand_logo.height() == window.brand_toolbar.height()
            assert window.brand_logo.pixmap().height() >= window.brand_toolbar.height() - 1
            for curve, plot in window.plots.curves.values():
                assert not curve.opts["autoDownsample"]
                assert curve.opts["downsample"] > 1
                assert curve.opts["downsampleMethod"] == "peak"
                assert len(curve.getData()[0]) < 4 * plot.vb.width(), "Full session was painted"
                assert len(curve.getOriginalDataset()[0]) == len(session.nr)
            y = window.plots.curves[(1, "ACC_X")][0].getData()[1]
            assert y.max() == 500 and y.min() == -400
            assert session.channels["ACC_X"].samples[50003] == 500
            cached = window.plots._masked_values["ACC_X"][0]
            assert cached is session.channels["ACC_X"].samples
            # Reassigning/changing selection must not copy full source arrays repeatedly.
            window.refresh_plots()
            assert window.plots._masked_values["ACC_X"][0] is cached
            window.axis_combo.setCurrentText("Distance")
            assert window.plots.mode == "distance"
            window.axis_combo.setCurrentText("Time")
            # A long gap makes global mean spacing misleading. Bound rendering by the
            # samples actually visible, even with nonuniform timestamps or stopped distance.
            session.elapsed_time_s[len(session.nr) // 2 :] += 1_000_000
            window.refresh_plots()
            window.plots.plots[0].setXRange(100, 300, padding=0)
            state["phase"] = "nonuniform"
        elif state["phase"] == "nonuniform":
            for curve, plot in window.plots.curves.values():
                assert len(curve.getData()[0]) < 4 * plot.vb.width()
            state["phase"] = "map_baseline"
        elif state["phase"] == "map_baseline":
            window.map_widget.page.runJavaScript(
                "routeRenderer.testRedraws=0;"
                "const originalRedraw=routeRenderer._redraw;"
                "routeRenderer._redraw=function(){this.testRedraws++;"
                "return originalRedraw.apply(this,arguments);};"
                "setMarker(0,0,123); setMarker(0.001,0.001,456);"
            )
            state["phase"] = "checking"
        elif state["phase"] == "checking":
            state["phase"] = "pending"
            window.map_widget.page.runJavaScript(
                "JSON.stringify({separate:marker._renderer!==route.getLayers()[0]._renderer,"
                "vertices:route.getLayers().reduce((sum,p)=>sum+(p.getLatLngs?p.getLatLngs().length:1),0),"
                "redraws:routeRenderer.testRedraws})",
                js_result,
            )
    except Exception as exc:
        fail(repr(exc))


timer.timeout.connect(check)
timer.start(250)
QTimer.singleShot(30000, lambda: fail("Timed out"))
raise SystemExit(app.exec())
