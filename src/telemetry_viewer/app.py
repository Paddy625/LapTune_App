import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QPixmapCache
from PySide6.QtWidgets import QApplication

from telemetry_viewer.gui.main_window import MainWindow
from telemetry_viewer.gui.theme import STYLE, apply_palette


def create_application(argv=None):
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("Telemetry Viewer")
    app.setStyle("Fusion")
    apply_palette(app)
    app.setStyleSheet(STYLE)
    # Several rasterized axes on a HiDPI screen exceed Qt's default 10 MiB cache.
    # Avoid evicting/repainting tick labels every time the shared cursor moves.
    QPixmapCache.setCacheLimit(max(QPixmapCache.cacheLimit(), 64 * 1024))
    return app


def isolated_settings():
    """Keep diagnostic GUI runs out of the user's saved layouts, including Windows registry."""
    directory = TemporaryDirectory(prefix="laptune-gui-settings-")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, directory.name)
    return directory


def main():
    parser = argparse.ArgumentParser(description="Local IMU/GPS telemetry analysis")
    parser.add_argument("file", nargs="?", type=Path, help="Logger TXT file or folder of TXT logs")
    diagnostics = parser.add_mutually_exclusive_group()
    diagnostics.add_argument(
        "--smoke-test",
        type=Path,
        metavar="SYNTHETIC_LOG",
        help="Offline GUI/render/map self-check; exits nonzero on failure",
    )
    diagnostics.add_argument(
        "--smoke-navigation",
        type=Path,
        metavar="SYNTHETIC_LOG",
        help="Offline CALC calculation and map self-check; exits nonzero on failure",
    )
    parser.add_argument("--smoke-result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    preferences = isolated_settings() if args.smoke_test or args.smoke_navigation else None
    app = create_application()
    window = MainWindow()
    window.show()
    if args.smoke_test:
        from telemetry_viewer.gui.smoke import run_smoke

        run_smoke(app, window, args.smoke_test, args.smoke_result)
    elif args.smoke_navigation:
        from telemetry_viewer.gui.smoke import run_navigation_smoke

        run_navigation_smoke(app, window, args.smoke_navigation, args.smoke_result)
    elif args.file:
        window.open_later(args.file)
    # Keep references alive through Qt's event loop.
    QTimer.singleShot(0, lambda: None)
    result = app.exec()
    if preferences is not None:
        window.settings.sync()
        preferences.cleanup()
    return result
