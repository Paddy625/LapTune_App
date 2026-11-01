"""Capture a genuine UI image using generated data only, never the user's route."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from generate_demo import generate
from PySide6.QtCore import QTimer

from telemetry_viewer.app import create_application, isolated_settings
from telemetry_viewer.gui.main_window import MainWindow
from telemetry_viewer.processing.signals import SignalRecipe, calculate_signal

preferences = isolated_settings()
app = create_application([])
window = MainWindow()
window.layer_combo.setCurrentText("Offline")
window.resize(1560, 920)
window.show()


def capture():
    if window.session is None or window.thread is not None or not window.map_widget.is_ready:
        QTimer.singleShot(100, capture)
        return
    if "--mixed" in sys.argv[2:]:
        window.plots.titles[0] = "Dynamics · acceleration / rotation / speed"
        window.channels.apply_plot_selection(0, ["ACC_X", "GYRO_Z", "GPS_SPEED_KMH"])
    if "--calculated" in sys.argv[2:]:
        recipe = SignalRecipe("CALC_ACC_X_LP5", "lowpass", ("ACC_X",), cutoff_hz=5)
        window.accept_calculated(calculate_signal(window.session.channels, recipe))
        window.count_combo.setCurrentIndex(1)
        window.plots.titles[0] = "Acceleration · measured / low-pass 5 Hz"
        window.channels.apply_plot_selection(0, ["ACC_X", recipe.name])
        window.plots.plots[0].setXRange(30, 120, padding=0)
    window.set_cursor(len(window.session.nr) // 2)
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    QTimer.singleShot(300, lambda: save(target))


def save(target):
    if not window.grab().save(str(target)):
        app.exit(1)
        return
    window.close()
    app.quit()


with TemporaryDirectory() as temp:
    path = Path(temp) / "Synthetic_demo.TXT"
    generate(path)
    window.load_file(path)
    QTimer.singleShot(500, capture)
    QTimer.singleShot(30000, lambda: app.exit(1))
    raise SystemExit(app.exec())
