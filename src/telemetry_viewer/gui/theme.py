"""LapTune's red, white and graphite palette for controls and plotted data."""

from PySide6.QtGui import QColor, QPalette

BACKGROUND = "#111214"
SURFACE = "#202328"
PANEL = "#181b1f"
BORDER = "#383e46"
TEXT = "#f2f3f5"
MUTED = "#b4bbc4"
ACCENT = "#ff1631"
CURSOR = "#ffffff"
# Distinct series colors retain readable multi-unit plots within the red-led UI.
SERIES_COLORS = ["#ff5265", "#e5e7eb", "#ffbc72", "#baabff", "#ff92b2", "#dfd58d", "#91c4ed"]

STYLE = """
QMainWindow,QDialog { background: #111214; color: #f2f3f5; }
QWidget { font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 12px; }
QMenuBar,QToolBar { background: #000000; color: #f2f3f5; border: 0; }
QStatusBar { background: #202328; color: #f2f3f5; border: 0; }
QMenuBar::item { padding: 4px 8px; }
QToolBar { spacing: 7px; padding: 9px; }
QMenuBar::item:selected,QMenu::item:selected { background: #762032; color: #fff; }
QMenu { background: #24282e; color: #f2f3f5; border: 1px solid #444b55; }
QToolButton,QComboBox,QPushButton,QLineEdit,QAbstractSpinBox {
 background: #292e35; color: #f2f3f5; border: 1px solid #424a55; border-radius: 4px;
 padding: 6px;
}
QToolButton:hover,QPushButton:hover { background: #363b43; border-color: #ff1631; }
QToolButton:pressed,QPushButton:pressed { background: #762032; }
QToolButton:disabled,QPushButton:disabled,QComboBox:disabled,QAbstractSpinBox:disabled {
 background: #202328; color: #7b828c; border-color: #30353d;
}
QToolButton[primary="true"] { background: #e9102a; color: #fff; border-color: #ff1631;
 font-weight: 600; }
QToolButton[primary="true"]:hover { background: #ff1631; border-color: #ff6a7c; }
QToolButton[primary="true"]:pressed { background: #ba0c21; }
QToolButton[primary="true"]:disabled { background: #54212b; color: #b29da1;
 border-color: #6d2a36; }
QComboBox::drop-down { border: 0; width: 18px; }
QComboBox QAbstractItemView { background: #292e35; color: #f2f3f5;
 selection-background-color: #762032; }
QDockWidget { color: #c7cdd5; font-weight: 600; }
QDockWidget::title { background: #252a31; padding: 9px; }
QTreeWidget,QTableWidget,QTextBrowser { background: #181b1f; alternate-background-color: #202328;
 color: #e4e7eb; border: 0; gridline-color: #383e46; }
QTreeWidget::item { padding: 5px 1px; }
QTreeWidget::item:selected { background: #54212b; }
QHeaderView::section { background: #292e35; color: #c7cdd5; border: 0; padding: 6px; }
QLabel { color: #d5dae1; }
QFrame#analysis_footer { background: #202328; border-top: 2px solid #ff1631; }
QLabel#analysis_heading { color: #f2f3f5; font-weight: 600; letter-spacing: 2px; }
QSplitter::handle { background: #343a43; }
QProgressBar { border: 1px solid #424a55; color: #f2f3f5; text-align: center; }
QProgressBar::chunk { background: #e9102a; }
QToolTip { background: #292e35; color: #f2f3f5; border: 1px solid #68717d; padding: 5px; }
QTabWidget::pane { border: 1px solid #383e46; background: #181b1f; }
QTabBar::tab { background: #202328; color: #b4bbc4; padding: 8px 14px;
 border-bottom: 2px solid #383e46; }
QTabBar::tab:selected { color: #fff; border-bottom-color: #ff1631; }
QScrollBar:vertical { background: #181b1f; width: 12px; margin: 0; }
QScrollBar:horizontal { background: #181b1f; height: 12px; margin: 0; }
QScrollBar::handle { background: #505966; border-radius: 4px; min-width: 24px; min-height: 24px; }
QScrollBar::handle:hover { background: #747e8d; }
QScrollBar::add-line,QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page,QScrollBar::sub-page { background: none; }
"""


def apply_palette(app):
    palette = app.palette()
    for role, color in (
        (QPalette.ColorRole.Window, BACKGROUND),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, PANEL),
        (QPalette.ColorRole.AlternateBase, SURFACE),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, "#292e35"),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Highlight, "#a7172e"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.ToolTipBase, "#292e35"),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.Link, "#ff7889"),
    ):
        palette.setColor(role, QColor(color))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#7b828c"))
    app.setPalette(palette)
