"""Offline Qt check for the new logger schema, GPS gaps and mixed-unit altitude plots."""

import sys
import time
import traceback
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer

from telemetry_viewer.app import create_application, isolated_settings
from telemetry_viewer.gui.main_window import MainWindow
from telemetry_viewer.gui.plot_config import PlotConfigurationDialog

preferences = isolated_settings()
app = create_application([])
window = MainWindow()
window.layer_combo.setCurrentText("Offline")
window.show()
window.load_file(Path(sys.argv[1]))
started = time.monotonic()
timer = QTimer(window)
checking_map = False


def finish(error=None):
    print("NEW_LOGGER_FAIL " + error if error else "NEW_LOGGER_PASS", flush=True)
    timer.stop()
    window.close()
    app.exit(1 if error else 0)


def verify_map(layers):
    try:
        assert layers >= 2, "GPS loss must split the displayed route"
        finish()
    except Exception:
        finish(traceback.format_exc())


def check():
    global checking_map
    try:
        if time.monotonic() - started > 40:
            raise TimeoutError("Waiting for new logger import or offline map")
        if checking_map or window.session is None or window.thread is not None:
            return
        if not window.map_widget.is_ready:
            return
        checking_map = True
        session = window.session
        item = window.channels.items["GPS_ALT_MSL_M"][0]
        assert item.parent().text(0) == "GPS"
        assert "[m]" in item.text(0)
        dialog = PlotConfigurationDialog(
            session, 0, [], "Altitude and speed", window.plots.y_ranges[0], window
        )
        for name, channel_item in dialog.channel_items.items():
            channel_item.setCheckState(
                0,
                Qt.CheckState.Checked
                if name in ("GPS_ALT_MSL_M", "GPS_SPEED_KMH")
                else Qt.CheckState.Unchecked,
            )
        QTimer.singleShot(20, dialog._accept)
        with patch("telemetry_viewer.gui.main_window.PlotConfigurationDialog", return_value=dialog):
            window.configure_plot(0)
        assert set(window.plots.unit_views[0]) == {"m", "km/h"}
        assert (0, "GPS_ALT_MSL_M") in window.plots.curves
        window.set_cursor(20)
        assert item.text(1) == "0.000"
        window.set_cursor(45)
        assert item.text(1) == "invalid", "Missing altitude must not become a held value"
        assert "—" in window.plots.cursor_readouts[0].toPlainText()
        window.map_widget.page.runJavaScript("route.getLayers().length", verify_map)
    except Exception:
        finish(traceback.format_exc())


timer.timeout.connect(check)
timer.start(150)
raise SystemExit(app.exec())
