"""Real offline GUI: driving wizard, worker, EST plots/map/exports and cancellation."""

import argparse
import csv
import hashlib
import json
import time
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree as ET

from generate_navigation_demo import write_demo
from PySide6.QtCore import Qt, QTimer

from telemetry_viewer.app import create_application, isolated_settings
from telemetry_viewer.gui.main_window import MainWindow
from telemetry_viewer.gui.navigation_dialog import NavigationDialog
from telemetry_viewer.gui.plot_config import PlotConfigurationDialog
from telemetry_viewer.processing.navigation import NavigationConfig

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--screenshot", type=Path)
args = parser.parse_args()
preferences = isolated_settings()
temporary = TemporaryDirectory(prefix="laptune-est-check-")
folder = Path(temporary.name)
path = write_demo(folder / "synthetic-navigation.TXT")
app = create_application([])
window = MainWindow()
window.layer_combo.setCurrentText("Offline")
window.resize(1560, 920)
window.count_combo.setCurrentIndex(1)
window.show()
window.load_file(path)
state = {"phase": "loading", "ticks": 0}
started = time.monotonic()
timer = QTimer(window)


def fail(message):
    print("NAVIGATION_GUI_FAIL " + state["phase"] + ": " + str(message), flush=True)
    timer.stop()
    app.exit(1)


original_error = window.calculation_error
window.calculation_error = fail


def verify_cancel():
    try:
        assert window.session.navigation is state["published"]
        assert window.calculation_thread is None
        print("NAVIGATION_GUI_PASS", flush=True)
    except Exception:
        print("NAVIGATION_GUI_FAIL cancellation: " + traceback.format_exc(), flush=True)
        app.exit(1)


def finish_success():
    try:
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
        state["published"] = window.session.navigation
        state["phase"] = "cancelling"
        timer.stop()
        window.calculation_error = original_error
        window.start_navigation(NavigationConfig())
        window.calculation_thread.finished.connect(verify_cancel)
        window.close()
    except Exception:
        fail(traceback.format_exc())


def verify_map(encoded):
    try:
        value = json.loads(encoded)
        assert value["layers"] > 0 and value["estimatedVisible"] and not value["rawVisible"]
        assert value["row"] == 1500 and value["radius"] > 0 and value["ticks"] == 4
        session = window.session
        assert (
            window.map_widget.sample_for_location(
                session.channels["EST_LAT"].samples[1500],
                session.channels["EST_LON"].samples[1500],
                1500,
            )
            == 1500
        )
        csv_path, gpx_path = folder / "est.csv", folder / "est.gpx"
        with patch(
            "telemetry_viewer.gui.main_window.QFileDialog.getSaveFileName",
            return_value=(str(csv_path), ""),
        ):
            window.export("csv")
        with csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == len(session.nr)
        assert "EST_ACC_X [m/s²]" in rows[0] and rows[0]["EST_ACC_X [m/s²]"] == ""
        metadata = json.loads(
            csv_path.with_suffix(".csv.metadata.json").read_text(encoding="utf-8")
        )
        assert metadata["navigation"]["axes"] == "X forward, Y left, Z up; world East, North, Up"
        assert metadata["navigation"]["vehicle_frame"] == "FLU"
        assert "positive forward" in metadata["channels"]["EST_ACC_X"]["description"]
        with patch(
            "telemetry_viewer.gui.main_window.QFileDialog.getSaveFileName",
            return_value=(str(gpx_path), ""),
        ):
            window.export("est_gpx")
        root = ET.parse(gpx_path).getroot()
        assert root.get("creator") == "LapTune EST"
        assert root.findall(".//{*}EST_NAV_STATUS") and root.find(".//{*}EST_report") is not None
        # Wait for the Qt/WebEngine compositor, not only the JavaScript callback, before capture.
        QTimer.singleShot(300, finish_success)
    except Exception:
        fail(traceback.format_exc())


def check():
    try:
        if time.monotonic() - started > 45:
            raise TimeoutError("Timed out")
        if state["phase"] == "loading":
            if (
                window.session is None
                or window.thread is not None
                or not window.map_widget.is_ready
            ):
                return
            session = window.session
            state["raw"] = {
                name: (id(c), hashlib.sha256(c.samples.tobytes()).digest())
                for name, c in session.channels.items()
            }
            dialog = NavigationDialog(session, "time", (0, 80), window)
            assert dialog.acceleration_unit.currentText() == "g"
            assert dialog.gyro_unit.currentText() == "deg/s"
            assert dialog.zero_bank.isChecked()
            dialog.allow_estimated.setChecked(False)
            dialog.resize(620, 580)

            def accept_navigation_dialog():
                try:
                    assert dialog.height() <= 650
                    assert dialog.buttons.geometry().bottom() < dialog.height()
                    dialog._accept()
                except Exception:
                    dialog.reject()
                    fail(traceback.format_exc())

            QTimer.singleShot(20, accept_navigation_dialog)
            with patch("telemetry_viewer.gui.main_window.NavigationDialog", return_value=dialog):
                window.navigation_button.click()
            assert window.calculation_thread is not None and not window.open_action.isEnabled()
            state["phase"] = "estimating"
        elif state["phase"] == "estimating":
            state["ticks"] += 1
            if window.session.navigation is None or window.calculation_thread is not None:
                return
            assert state["ticks"] >= 1
            session = window.session
            assert window.count_combo.currentIndex() == 1 and window.plots._count == 2
            assert window.open_action.isEnabled() and window.navigation_action.isEnabled()
            for name, (identity, digest) in state["raw"].items():
                assert id(session.channels[name]) == identity
                assert hashlib.sha256(session.channels[name].samples.tobytes()).digest() == digest
            assert all(name.startswith("EST_") for name in session.navigation.channels)
            item = window.channels.items["EST_ACC_X"][0]
            assert item.parent().text(0) == "Calculated" and "positive forward" in item.toolTip(0)
            selected = ["GPS_SPEED_KMH", "EST_SPEED_KMH", "EST_ACC_X"]
            dialog = PlotConfigurationDialog(session, 0, [], "GPS / EST comparison", {}, window)
            for name, item in dialog.channel_items.items():
                item.setCheckState(
                    0, Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked
                )
            QTimer.singleShot(20, dialog._accept)
            with patch(
                "telemetry_viewer.gui.main_window.PlotConfigurationDialog", return_value=dialog
            ):
                window.configure_plot(0)
            assert set(window.plots.unit_views[0]) == {"km/h", "m/s²"}
            window.channels.apply_plot_selection(1, ["EST_POS_95_M"])
            window.track_combo.setCurrentText("EST")
            assert window.track_action.isVisible()
            window.set_cursor(1500)
            window.plots.plots[0].setXRange(29, 33, padding=0)
            state["phase"] = "map"
        elif state["phase"] == "map":
            state["phase"] = "checking_js"
            window.update_map_range()
            window.map_widget.page.runJavaScript(
                "JSON.stringify({layers:estimatedRoute.getLayers().length,estimatedVisible:map.hasLayer(estimatedRoute),"
                "rawVisible:map.hasLayer(route),row:estimatedRow,radius:estimatedUncertainty.getRadius(),ticks:rangeMarks.getLayers().length})",
                verify_map,
            )
    except Exception:
        fail(traceback.format_exc())


timer.timeout.connect(check)
timer.start(100)
raise SystemExit(app.exec())
