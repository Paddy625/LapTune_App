"""Capture a genuine UI image using generated data only, never the user's route."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from generate_demo import generate
from PySide6.QtCore import QTimer

from telemetry_viewer.app import create_application
from telemetry_viewer.gui.main_window import MainWindow

app = create_application([])
window = MainWindow()
window.layer_combo.setCurrentText("Offline")
window.resize(1560, 920)
window.show()


def capture():
    if window.session is None or window.thread is not None or not window.map_widget.is_ready:
        QTimer.singleShot(100, capture)
        return
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
