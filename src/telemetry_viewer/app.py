import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from telemetry_viewer.gui.main_window import MainWindow

STYLE = """
QMainWindow,QDialog { background: #101923; color: #dce6f0; }
QWidget { font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 12px; }
QMenuBar,QToolBar,QStatusBar { background: #172330; color: #cfdeed; border: 0; }
QToolBar { spacing: 7px; padding: 9px; border-bottom: 1px solid #2b3a4a; }
QMenuBar::item:selected,QMenu::item:selected { background: #314a60; }
QMenu { background: #1c2b3b; color: #dfebf6; border: 1px solid #3a4d60; }
QToolButton,QComboBox,QPushButton,QLineEdit {
 background: #233446; color: #e1eaf4; border: 1px solid #354c62; border-radius: 4px;
 padding: 6px;
}
QToolButton:hover,QPushButton:hover { background: #305368; border-color: #40c9b7; }
QComboBox::drop-down { border: 0; width: 18px; }
QComboBox QAbstractItemView { background: #233446; color: #e1eaf4;
 selection-background-color: #355e73; }
QDockWidget { color: #8dabc3; font-weight: 600; }
QDockWidget::title { background: #1b2a3a; padding: 9px; }
QTreeWidget,QTableWidget,QTextBrowser { background: #14202d; alternate-background-color: #1a2939;
 color: #d4dfeb; border: 0; gridline-color: #2b4054; }
QTreeWidget::item { padding: 5px 1px; }
QTreeWidget::item:selected { background: #2b465b; }
QHeaderView::section { background: #1f3042; color: #9fb4c9; border: 0; padding: 6px; }
QLabel { color: #c6d7e8; }
QSplitter::handle { background: #293c4e; }
QProgressBar { border: 1px solid #385168; color: #dce6f0; text-align: center; }
QProgressBar::chunk { background: #249c91; }
QToolTip { background: #21394b; color: #f0f7ff; border: 1px solid #50738e; padding: 5px; }
"""


def create_application(argv=None):
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("Telemetry Viewer")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    return app


def main():
    parser = argparse.ArgumentParser(description="Local IMU/GPS telemetry analysis")
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument(
        "--smoke-test",
        type=Path,
        metavar="SYNTHETIC_LOG",
        help="Offline GUI/render/map self-check; exits nonzero on failure",
    )
    parser.add_argument("--smoke-result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    app = create_application()
    window = MainWindow()
    window.show()
    if args.smoke_test:
        from telemetry_viewer.gui.smoke import run_smoke

        run_smoke(app, window, args.smoke_test, args.smoke_result)
    elif args.file:
        window.open_later(args.file)
    # Keep references alive through Qt's event loop.
    QTimer.singleShot(0, lambda: None)
    return app.exec()
