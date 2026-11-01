import html
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from telemetry_viewer.exporters.files import export_csv, export_gpx
from telemetry_viewer.gui.channel_panel import ChannelPanel
from telemetry_viewer.gui.plot_config import PlotConfigurationDialog
from telemetry_viewer.gui.plot_panel import PlotPanel
from telemetry_viewer.gui.signal_tools import SignalToolsDialog, StatisticsDialog
from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter
from telemetry_viewer.maps.map_widget import MapWidget
from telemetry_viewer.processing.display import elapsed_clock
from telemetry_viewer.processing.signals import calculate_signal


class ImportWorker(QObject):
    progress = Signal(int)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, path):
        super().__init__()
        self.path = path

    @Slot()
    def run(self):
        try:

            def report(percent):
                if QThread.currentThread().isInterruptionRequested():
                    raise InterruptedError("Import cancelled")
                self.progress.emit(percent)

            session = LegacyTxtImporter().load(Path(self.path), report)
            self.result.emit(session)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()


class CalculationWorker(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, channels, recipe):
        super().__init__()
        self.channels, self.recipe = channels, recipe

    @Slot()
    def run(self):
        try:

            def check_cancelled():
                if QThread.currentThread().isInterruptionRequested():
                    raise InterruptedError("Calculation cancelled")

            channel = calculate_signal(self.channels, self.recipe, check_cancelled)
            check_cancelled()
            self.result.emit(channel)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    session_loaded = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Telemetry Viewer")
        self.resize(1560, 920)
        self.setMinimumSize(1000, 600)
        self.settings = QSettings(
            QSettings.defaultFormat(), QSettings.Scope.UserScope, "LapTune", "TelemetryViewer"
        )
        self.session = None
        self.source_path = None
        self.cursor_sample = 0
        self.thread = None
        self.calculation_thread = None
        self.closing = False
        self.plots = PlotPanel()
        self._loading_layout = False
        try:
            self._saved_layout = json.loads(self.settings.value("plot_layout_v2", "{}"))
        except (TypeError, ValueError):
            self._saved_layout = {}
        if not isinstance(self._saved_layout, dict):
            self._saved_layout = {}
        if not isinstance(self._saved_layout.get("plots", []), list):
            self._saved_layout = {}
        for i, saved in enumerate(self._saved_layout.get("plots", [])[:4]):
            if not isinstance(saved, dict):
                continue
            self.plots.titles[i] = str(saved.get("title", f"Plot {i + 1}"))[:128]
            ranges = saved.get("ranges", {})
            if not isinstance(ranges, dict):
                continue
            for unit, limits in ranges.items():
                if (
                    isinstance(limits, list)
                    and len(limits) == 2
                    and all(isinstance(v, (float, int)) and np.isfinite(v) for v in limits)
                    and limits[0] < limits[1]
                ):
                    self.plots.manual_ranges[i][unit] = tuple(limits)
        self.plot_content = QWidget()
        self.plot_layout = QVBoxLayout(self.plot_content)
        self.plot_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_layout.setSpacing(0)
        self.plot_layout.addWidget(self.plots, 1)
        self.setCentralWidget(self.plot_content)
        self.channels = ChannelPanel()
        self.channel_content = QWidget()
        self.channel_layout = QVBoxLayout(self.channel_content)
        self.channel_layout.setContentsMargins(0, 0, 0, 0)
        self.channel_layout.setSpacing(0)
        self.channel_layout.addWidget(self.channels, 1)
        self.channel_dock = QDockWidget("CHANNELS", self)
        self.channel_dock.setWidget(self.channel_content)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.channel_dock)
        self.map_widget = MapWidget()
        self.map_content = QWidget()
        self.map_layout = QVBoxLayout(self.map_content)
        self.map_layout.setContentsMargins(0, 0, 0, 0)
        self.map_layout.setSpacing(0)
        self.map_layout.addWidget(self.map_widget, 1)
        self.map_dock = QDockWidget("GPS ROUTE · nearest recorded fix", self)
        self.map_dock.setWidget(self.map_content)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.map_dock)
        self.resizeDocks([self.channel_dock, self.map_dock], [400, 380], Qt.Orientation.Horizontal)
        self.channels.selection_updated.connect(self.refresh_plots)
        self.channels.calculate_requested.connect(self.show_signal_tools)
        self.plots.position_changed.connect(self.on_plot_position)
        self.plots.configure_requested.connect(self.configure_plot)
        self.plots.layout_changed.connect(self.save_plot_layout)
        self.map_widget.location_selected.connect(self.on_map_position)
        self._build_actions()
        self._build_channel_actions()
        self.range_timer = QTimer(self)
        self.range_timer.setSingleShot(True)
        self.range_timer.setInterval(30)
        self.range_timer.timeout.connect(self.update_map_range)
        self.plots.plots[0].vb.sigXRangeChanged.connect(self.schedule_map_range)
        self.summary = QLabel("Open a logger .TXT file to begin · all processing stays local")
        self.summary.setWordWrap(True)
        self.quality_button = QToolButton()
        self.quality_button.setObjectName("quality_report")
        self.quality_button.setDefaultAction(self.quality_action)
        self.statusBar().addWidget(self.quality_button)
        self.statusBar().addWidget(self.summary, 1)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(150)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.cursor_label = QLabel("Cursor —")
        self.statusBar().addPermanentWidget(self.cursor_label)
        self.setAcceptDrops(True)
        geometry = self.settings.value("geometry")
        state = self.settings.value("dock_state")
        # Stable object names are required by QMainWindow's state serialization.
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)
        self.removeToolBarBreak(self.brand_toolbar)
        self.layer_combo.setCurrentText(self.settings.value("map_layer", "Road"))

    def _build_actions(self):
        self.channel_dock.setObjectName("channels")
        self.map_dock.setObjectName("gps_map")
        file_menu = self.menuBar().addMenu("&File")
        self.open_action = QAction("Open file…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.choose_file)
        self.open_folder_action = QAction("Open folder…", self)
        self.open_folder_action.setShortcut("Ctrl+Shift+O")
        self.open_folder_action.triggered.connect(self.choose_folder)
        file_menu.addAction(self.open_folder_action)
        file_menu.addAction(self.open_action)
        self.recent_menu = file_menu.addMenu("Recent files and folders")
        self._refresh_recent()
        self.export_menu = QMenu("Export", self)
        self.export_menu.addAction("Selected channels to CSV…", lambda: self.export("csv"))
        self.export_menu.addAction("GPS route to GPX…", lambda: self.export("gpx"))
        file_menu.addMenu(self.export_menu)
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.close, QKeySequence.StandardKey.Quit)
        view = self.menuBar().addMenu("&View")
        view.addAction(self.channel_dock.toggleViewAction())
        view.addAction(self.map_dock.toggleViewAction())
        view.addAction("Fit X to session", self.plots.fit_all, "Ctrl+0")
        session_menu = self.menuBar().addMenu("&Session")
        session_menu.addAction("Metadata and map provider…", self.configure)
        session_menu.addAction("Data quality and distance report…", self.show_quality)
        self.signal_tools_action = QAction("Calculate / filter signals…", self)
        self.signal_tools_action.triggered.connect(self.show_signal_tools)
        self.statistics_action = QAction("Signal statistics…", self)
        self.statistics_action.triggered.connect(self.show_statistics)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("Controls and privacy", self.show_help)
        self.settings_action = QAction("Settings", self)
        self.settings_action.triggered.connect(self.configure)
        self.menuBar().insertAction(help_menu.menuAction(), self.settings_action)
        self.menuBar().insertMenu(help_menu.menuAction(), self.export_menu)
        self.quality_action = QAction("Quality report", self)
        self.quality_action.triggered.connect(self.show_quality)
        toolbar = QToolBar("Controls")
        self.analysis_toolbar = toolbar
        toolbar.setObjectName("analysis")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addAction(self.open_folder_action)
        toolbar.addAction(self.open_action)
        recent = QToolButton()
        recent.setText("Recent")
        recent.setMenu(self.recent_menu)
        recent.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(recent)
        self.plot_controls = QToolBar("Plot controls", self.plot_content)
        self.plot_controls.setObjectName("plot_controls")
        self.plot_controls.setMovable(False)
        self.plot_controls.setFloatable(False)
        self.plot_layout.addWidget(self.plot_controls)
        self.axis_combo = QComboBox()
        self.axis_combo.addItems(["Time", "Distance"])
        self.axis_combo.currentIndexChanged.connect(self.axis_changed)
        self.plot_controls.addWidget(self.axis_combo)
        self.distance_combo = QComboBox()
        self.distance_combo.addItems(["Speed integral", "GPS geodesic"])
        self.distance_combo.setToolTip("Distance method; compare totals in Session > Data quality")
        self.distance_combo.currentIndexChanged.connect(self.distance_changed)
        self.plot_controls.addWidget(self.distance_combo)
        self.fit_x_action = self.plot_controls.addAction("Fit X", self.plots.fit_all)
        self.count_combo = QComboBox()
        self.count_combo.addItems(["1 plot", "2 plots", "3 plots", "4 plots"])
        count = self._saved_layout.get("count", 3)
        count = count if isinstance(count, int) and 1 <= count <= 4 else 3
        self.count_combo.setCurrentIndex(count - 1)
        self.plots.set_count(count)
        self.count_combo.currentIndexChanged.connect(self.plot_count_changed)
        self.plot_controls.addWidget(self.count_combo)
        self.plot_controls.setMinimumWidth(self.plot_controls.sizeHint().width())
        self.map_controls = QToolBar("Map controls", self.map_content)
        self.map_controls.setObjectName("map_controls")
        self.map_controls.setMovable(False)
        self.map_controls.setFloatable(False)
        self.map_layout.addWidget(self.map_controls)
        self.layer_combo = QComboBox()
        self.layer_combo.addItems(["Road", "Offline", "Satellite"])
        self.layer_combo.currentTextChanged.connect(self.layer_changed)
        self.map_controls.addWidget(self.layer_combo)
        self.map_controls.setMinimumWidth(self.map_controls.sizeHint().width())
        # A dedicated toolbar keeps branding visible even when analysis controls overflow.
        self.brand_toolbar = QToolBar("LapTune")
        self.brand_toolbar.setObjectName("branding")
        self.brand_toolbar.setMovable(False)
        self.brand_toolbar.setFloatable(False)
        self.brand_toolbar.setMinimumWidth(314)
        self.brand_toolbar.setFixedHeight(60)
        self.brand_toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.brand_toolbar.setStyleSheet(
            "QToolBar {padding:0; margin:0; border:0; spacing:0; background:#000000;}"
        )
        self.brand_logo = QLabel()
        self.brand_logo.setObjectName("laptune_brand")
        self.brand_logo.setAccessibleName("LapTune")
        self.brand_logo.setToolTip("LapTune · Telemetry Viewer")
        logo = QPixmap(str(Path(__file__).parent / "assets" / "laptune-logo.png"))
        logo = logo.copy(113, 172, 1943, 377)
        self.brand_logo.setPixmap(
            logo.scaled(
                310,
                60,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.brand_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_logo.setFixedSize(310, 60)
        self.brand_logo.setStyleSheet("background:#000; padding:0; margin:0; border:0;")
        brand_spacer = QWidget()
        brand_spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.brand_toolbar.addWidget(brand_spacer)
        self.brand_toolbar.addWidget(self.brand_logo)
        self.addToolBar(self.brand_toolbar)

    def _build_channel_actions(self):
        self.analysis_footer = QFrame()
        self.analysis_footer.setObjectName("analysis_footer")
        footer = QVBoxLayout(self.analysis_footer)
        footer.setContentsMargins(12, 12, 12, 12)
        footer.setSpacing(8)
        heading = QLabel("ANALYSIS")
        heading.setObjectName("analysis_heading")
        footer.addWidget(heading)
        self.calculate_button = QToolButton()
        self.calculate_button.setObjectName("calculate_signals")
        self.calculate_button.setProperty("primary", True)
        self.calculate_button.setDefaultAction(self.signal_tools_action)
        self.calculate_button.setToolTip("Calculate or filter the selected channel")
        self.calculate_button.setMinimumHeight(38)
        self.calculate_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        footer.addWidget(self.calculate_button)
        self.statistics_button = QToolButton()
        self.statistics_button.setObjectName("signal_statistics")
        self.statistics_button.setDefaultAction(self.statistics_action)
        self.statistics_button.setMinimumHeight(32)
        self.statistics_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        footer.addWidget(self.statistics_button)
        self.channel_layout.addWidget(self.analysis_footer)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "brand_toolbar"):
            # Let analysis actions overflow before the fixed-height logo is squeezed out.
            available = event.size().width() - self.brand_toolbar.minimumWidth()
            self.analysis_toolbar.setMaximumWidth(max(240, available))

    def _refresh_recent(self):
        self.recent_menu.clear()
        for path in self.settings.value("recent", [], type=list):
            label = Path(path).name + (" (folder)" if Path(path).is_dir() else "")
            self.recent_menu.addAction(label, lambda p=path: self.load_file(p))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear history", self.clear_recent)

    def clear_recent(self):
        self.settings.setValue("recent", [])
        self._refresh_recent()

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open logger data", "", "Logger files (*.txt *.TXT)"
        )
        if path:
            self.load_file(path)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Open folder of logger TXT files")
        if path:
            self.load_file(path)

    def load_file(self, path):
        if self.thread is not None or self.calculation_thread is not None:
            return
        self.pending_path = Path(path).resolve()
        self.open_action.setEnabled(False)
        self.open_folder_action.setEnabled(False)
        self.signal_tools_action.setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self.thread = QThread(self)
        self.worker = ImportWorker(path)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.result.connect(self.accept_session)
        self.worker.error.connect(self.import_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.import_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def import_error(self, message):
        if not self.closing:
            QMessageBox.warning(self, "Could not import file", message)

    def import_finished(self):
        self.thread = None
        self.open_action.setEnabled(True)
        self.open_folder_action.setEnabled(True)
        self.signal_tools_action.setEnabled(True)
        self.progress.hide()
        if self.closing:
            self.close()

    def accept_session(self, session):
        self.session = session
        self.source_path = self.pending_path
        self.cursor_sample = 0
        session.metadata.timezone = self.settings.value("timezone", "")
        session.metadata.orientation = self.settings.value("orientation", "Unspecified sensor axes")
        self.apply_units(
            self.settings.value("acc_unit", "g (assumed)"),
            self.settings.value("gyro_unit", "deg/s (assumed)"),
        )
        self.distance_combo.blockSignals(True)
        self.distance_combo.setCurrentIndex(0 if session.distance_speed_m is not None else 1)
        self.distance_combo.blockSignals(False)
        saved_plots = self._saved_layout.get("plots", [])
        assignments = None
        if saved_plots:
            assignments = [
                p.get("channels", [])
                if isinstance(p, dict) and isinstance(p.get("channels", []), list)
                else []
                for p in saved_plots[:4]
            ]
            assignments += [[] for _ in range(4 - len(assignments))]
        self._loading_layout = True
        self.channels.populate(session, assignments)
        self._loading_layout = False
        self.map_widget.set_session(session)
        self.plots.fit_all()
        self.set_cursor(0)
        self.schedule_map_range()
        self.setWindowTitle(f"{session.filename} — Telemetry Viewer")
        self.update_summary()
        recent = self.settings.value("recent", [], type=list)
        path = str(self.source_path)
        self.settings.setValue("recent", [path] + [p for p in recent if p != path][:7])
        self._refresh_recent()
        self.session_loaded.emit()

    def update_summary(self):
        s = self.session
        if s is None:
            return
        duration = s.elapsed_time_s[-1]
        hz = (len(s.nr) - 1) / duration if duration else 0
        speed = s.channels.get("GPS_SPEED_KMH")
        valid = speed.samples[speed.valid] if speed else np.array([])
        speed_text = f" · {valid.min():.1f}–{valid.max():.1f} km/h" if len(valid) else ""
        self.summary.setText(
            f"{s.filename}  ·  {len(s.nr):,} samples  ·  {duration:.3f} s"
            f"  ·  {hz:.1f} rows/s  ·  {s.distance_m[-1] / 1000:.3f} km"
            f"{speed_text}  ·  {len(s.issues)} warnings"
        )

    def refresh_plots(self, *_):
        if self.session is None:
            return
        selection = self.channels.selection()
        self.plots.set_selection(self.session, selection, self.axis_mode)
        self.set_cursor(self.cursor_sample)
        self.save_plot_layout()

    def configure_plot(self, panel, tab=0):
        if self.session is None:
            QMessageBox.information(self, "Open data first", "Open a logger file or folder first.")
            return
        names = [name for name, p, _color in self.channels.selection() if p == panel]
        dialog = PlotConfigurationDialog(
            self.session,
            panel,
            names,
            self.plots.titles[panel],
            self.plots.y_ranges[panel],
            self,
            tab=tab,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.plots.titles[panel] = dialog.title_edit.text().strip()[:128] or f"Plot {panel + 1}"
            # Record ranges first so adding a channel cannot overwrite the chosen limits.
            self.plots.manual_ranges[panel].update(dialog.ranges())
            self.channels.apply_plot_selection(panel, dialog.selected_names())
            self.plots.set_y_ranges(panel, dialog.ranges())
            self.save_plot_layout()

    def save_plot_layout(self):
        if self.session is None or self._loading_layout:
            return
        selection = self.channels.selection()
        self._saved_layout = {
            "count": self.count_combo.currentIndex() + 1,
            "plots": [
                {
                    "title": self.plots.titles[p],
                    "channels": [name for name, panel, _ in selection if panel == p],
                    "ranges": self.plots.manual_ranges[p],
                }
                for p in range(4)
            ],
        }
        self.settings.setValue("plot_layout_v2", json.dumps(self._saved_layout))

    def plot_count_changed(self, index):
        self.plots.set_count(index + 1)
        self.save_plot_layout()

    def show_signal_tools(self, source=""):
        if self.session is None:
            QMessageBox.information(self, "Open data first", "Open a logger file or folder first.")
            return
        if self.thread is not None or self.calculation_thread is not None:
            return
        if not isinstance(source, str) or source not in self.session.channels:
            selected = self.channels.currentItem()
            source = selected.data(0, Qt.ItemDataRole.UserRole) if selected is not None else ""
        dialog = SignalToolsDialog(
            self.session,
            self,
            source=source or "",
            mode=self.axis_mode,
            bounds=tuple(self.plots.plots[0].vb.viewRange()[0]),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.start_calculation(dialog.recipe())

    def start_calculation(self, recipe):
        if self.session is None or self.thread is not None or self.calculation_thread is not None:
            return
        self.open_action.setEnabled(False)
        self.open_folder_action.setEnabled(False)
        self.signal_tools_action.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.calculation_thread = QThread(self)
        self.calculation_worker = CalculationWorker(dict(self.session.channels), recipe)
        self.calculation_worker.moveToThread(self.calculation_thread)
        self.calculation_thread.started.connect(self.calculation_worker.run)
        self.calculation_worker.result.connect(self.accept_calculated)
        self.calculation_worker.error.connect(self.calculation_error)
        self.calculation_worker.finished.connect(self.calculation_thread.quit)
        self.calculation_worker.finished.connect(self.calculation_worker.deleteLater)
        self.calculation_thread.finished.connect(self.calculation_finished)
        self.calculation_thread.finished.connect(self.calculation_thread.deleteLater)
        self.calculation_thread.start()

    def accept_calculated(self, channel):
        if self.closing:
            return
        assignments = [
            [name for name, panel, _ in self.channels.selection() if panel == p] for p in range(4)
        ]
        self.session.channels[channel.name] = channel
        self.channels.populate(self.session, assignments)
        self.statusBar().showMessage(
            f"Created {channel.name} in Calculated · choose Configure plot… to display it", 10000
        )

    def calculation_error(self, message):
        if not self.closing:
            QMessageBox.warning(self, "Could not calculate signal", message)

    def calculation_finished(self):
        self.calculation_thread = None
        self.open_action.setEnabled(True)
        self.open_folder_action.setEnabled(True)
        self.signal_tools_action.setEnabled(True)
        self.progress.hide()
        self.progress.setRange(0, 100)
        if self.closing:
            self.close()

    def show_statistics(self):
        if self.session is None:
            QMessageBox.information(self, "Open data first", "Open a logger file or folder first.")
            return
        bounds = self.plots.plots[0].vb.viewRange()[0]
        StatisticsDialog(self.session, self.axis_mode, bounds, self).exec()

    def schedule_map_range(self, *_):
        if not self.range_timer.isActive():
            self.range_timer.start()

    def update_map_range(self):
        if self.session is not None:
            lo, hi = self.plots.plots[0].vb.viewRange()[0]
            self.map_widget.set_visible_range(lo, hi, self.axis_mode)

    @property
    def axis_mode(self):
        return "time" if self.axis_combo.currentIndex() == 0 else "distance"

    def axis_changed(self, *_):
        self.refresh_plots()
        self.plots.fit_all()
        self.schedule_map_range()

    def distance_changed(self, index):
        if self.session is None:
            return
        if index == 0 and self.session.distance_speed_m is None:
            QMessageBox.information(
                self, "Speed unavailable", "No usable GPS speed; using GPS distance."
            )
            self.distance_combo.setCurrentIndex(1)
            return
        self.session.distance_method = "speed" if index == 0 else "gps"
        self.axis_changed()
        self.update_summary()

    def on_plot_position(self, x):
        if self.session is not None:
            self.set_cursor(self.session.sample_at(x, self.axis_mode, self.cursor_sample))

    def on_map_position(self, lat, lon):
        if self.session is not None:
            sample = self.session.sample_for_location(lat, lon, self.cursor_sample)
            if sample is not None:
                self.set_cursor(sample)
                x = float(self.session.axis(self.axis_mode)[sample])
                lo, hi = self.plots.plots[0].viewRange()[0]
                if x < lo or x > hi:
                    width = max(hi - lo, 1)
                    self.plots.plots[0].setXRange(x - width / 2, x + width / 2, padding=0)

    def set_cursor(self, sample):
        if self.session is None:
            return
        self.cursor_sample = int(np.clip(sample, 0, len(self.session.nr) - 1))
        s, i = self.session, self.cursor_sample
        self.plots.set_cursor(float(s.axis(self.axis_mode)[i]))
        self.channels.set_cursor(i)
        self.map_widget.set_cursor(i)
        self.cursor_label.setText(
            f"Row {i:,} · NR {s.nr[i]} · {elapsed_clock(s.elapsed_time_s[i], True)} "
            f"· {s.distance_m[i]:.1f} m"
        )
        source = s.source_for_sample(i)
        if source is not None:
            self.cursor_label.setText(self.cursor_label.text() + f" · {source.path.name}")

    def layer_changed(self, mode):
        url = self.settings.value("satellite_url", "")
        attribution = self.settings.value("satellite_attribution", "")
        if mode == "Satellite" and not (url and attribution):
            QMessageBox.information(
                self,
                "Satellite provider required",
                "Set a licensed HTTPS tile URL and attribution in Settings. "
                "Road and Offline modes work without a key.",
            )
            self.layer_combo.setCurrentText("Road")
            return
        self.map_widget.set_layer(mode, url, html.escape(attribution))
        self.settings.setValue("map_layer", mode)

    def apply_units(self, acc, gyro):
        if self.session is None:
            return
        self.session.metadata.acceleration_unit = acc
        self.session.metadata.gyroscope_unit = gyro
        for channel in self.session.channels.values():
            if channel.group == "Acceleration":
                channel.unit = acc
            elif channel.group == "Gyroscope":
                channel.unit = gyro

    def configure(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Session metadata and map provider")
        form = QFormLayout(dialog)
        fields = {}
        for key, label, default in [
            ("timezone", "Logger timezone (IANA, blank = unspecified)", ""),
            ("orientation", "Sensor orientation description", "Unspecified sensor axes"),
            ("acc_unit", "Acceleration unit (label only; no conversion)", "g (assumed)"),
            ("gyro_unit", "Gyroscope unit (label only; no conversion)", "deg/s (assumed)"),
            ("satellite_url", "Licensed satellite HTTPS URL with {z}/{x}/{y}", ""),
            ("satellite_attribution", "Satellite attribution (plain text)", ""),
        ]:
            fields[key] = QLineEdit(self.settings.value(key, default))
            form.addRow(label, fields[key])
        note = QLabel(
            "Settings stay on this computer. Tile URLs may include a provider key.\n"
            "Raw logger times are preserved. Identical GPS frames cannot be distinguished.\n"
            "No fixed IMU/GPS rate is assumed. Unit changes relabel values only."
        )
        form.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def save():
            tz = fields["timezone"].text().strip()
            try:
                if tz:
                    ZoneInfo(tz)
            except (ZoneInfoNotFoundError, ValueError):
                QMessageBox.warning(
                    dialog, "Invalid timezone", "Use an IANA name, e.g. Europe/Warsaw"
                )
                return
            url = fields["satellite_url"].text().strip()
            if url and (
                urlparse(url).scheme != "https"
                or not urlparse(url).hostname
                or not all(token in url for token in ("{x}", "{y}", "{z}"))
                or not fields["satellite_attribution"].text().strip()
            ):
                QMessageBox.warning(
                    dialog, "Invalid provider", "Use an HTTPS tile template and attribution."
                )
                return
            for key, widget in fields.items():
                self.settings.setValue(key, widget.text().strip())
            if self.session:
                self.session.metadata.timezone = tz
                self.session.metadata.orientation = fields["orientation"].text()
                assignments = [
                    [name for name, panel, _ in self.channels.selection() if panel == p]
                    for p in range(4)
                ]
                self.apply_units(fields["acc_unit"].text(), fields["gyro_unit"].text())
                self.channels.populate(self.session, assignments)
            self.layer_changed(self.layer_combo.currentText())
            dialog.accept()

        buttons.accepted.connect(save)
        dialog.exec()

    def show_quality(self):
        if self.session is None:
            return
        s = self.session
        dialog = QDialog(self)
        dialog.setWindowTitle("Data quality · recorded order preserved")
        dialog.resize(820, 650)
        layout = QVBoxLayout(dialog)
        report = QTextBrowser()
        counts = Counter(i.code for i in s.issues)
        speed = (
            f"{s.distance_speed_m[-1]:.3f} m" if s.distance_speed_m is not None else "unavailable"
        )
        rows = "".join(f"<li>{html.escape(k)}: {v}</li>" for k, v in sorted(counts.items()))
        report.setHtml(
            f"<h3>Distance comparison</h3><p>Speed integral: {speed}<br>"
            f"GPS geodesic: {s.distance_gps_m[-1]:.3f} m<br>"
            f"Accepted GPS changes: {len(s.gps_fix_samples)} (not physical frame count)</p>"
            f"<p>{html.escape(s.metadata.time_correction)}. Invalid time retains rows. "
            "Speed uses last recorded value until the next row; gaps are reported. "
            "GPS jumps above 100 m/s are excluded. Coordinate gaps split the route.</p>"
            f"<ul>{rows}</ul><p>Each channel has its own recorded time axis. "
            "Row density is not a sensor's physical refresh rate; unchanged values "
            "alone cannot prove whether a new measurement arrived.</p>"
        )
        layout.addWidget(report)
        source_list = "".join(
            f"<li>{html.escape(source.path.name)}: "
            f"{source.end_row - source.start_row:,} samples</li>"
            for source in s.source_files
        )
        report.append(f"<h3>Files in acquisition order</h3><ol>{source_list}</ol>")
        table = QTableWidget(len(s.issues), 4)
        table.setHorizontalHeaderLabels(["Source file", "File line", "Code", "Diagnostic"])
        for row, issue in enumerate(s.issues):
            for col, text in enumerate([issue.source, str(issue.line), issue.code, issue.message]):
                table.setItem(row, col, QTableWidgetItem(text))
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def export(self, kind):
        if self.session is None:
            return
        names = list(dict.fromkeys(name for name, _, _ in self.channels.selection()))
        if kind == "csv" and not names:
            QMessageBox.information(
                self, "Select channels", "Select at least one channel to export."
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export " + kind.upper(),
            str(Path.home() / f"telemetry.{kind}"),
            f"{kind.upper()} (*.{kind})",
        )
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix("." + kind)
        if self.is_source_file(target) or self.is_source_file(
            target.with_suffix(target.suffix + ".metadata.json")
        ):
            QMessageBox.warning(
                self, "Protected input", "Choose an output different from the input log."
            )
            return
        try:
            if kind == "csv":
                export_csv(self.session, target, names)
            else:
                export_gpx(self.session, target)
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
        else:
            self.statusBar().showMessage("Export saved: " + target.name, 6000)

    def is_source_file(self, path):
        resolved = Path(path).resolve()
        return resolved == self.source_path or any(
            resolved == source.path for source in self.session.source_files
        )

    def show_help(self):
        QMessageBox.information(
            self,
            "Controls and privacy",
            "Ctrl+O opens a file; Ctrl+Shift+O opens a folder of TXT logs "
            "in numeric filename order. "
            "Drop a file or folder to import. Wheel and dragging change X only; "
            "Ctrl+0 fits the X axis. Use Configure plot… / Y-axis ranges… to change Y limits. "
            "Hover/click plots "
            "to move cursor. Click the map to select the nearest GPS change; "
            "repeated passages prefer the active time.\n\n"
            "Configure plot… selects any channels, title and per-unit Y ranges. "
            "A channel can appear in several plots. Each distinct unit gets its own Y scale. "
            "Time ticks use hh:mm:ss. Layouts and Y limits are saved locally. "
            "Channel context menu: solo/hide all. Panels share X range. "
            "Calculate / filter signals at the bottom of Channels creates channels in Calculated. "
            "Signal statistics there uses full-resolution samples "
            "in the visible range or session. "
            "Green/pink map ticks show the visible X range's first/last recorded GPS positions. "
            "Distance plateaus preserve the active time where possible.\n\n"
            "Logs remain local. Online maps request tiles around the route, "
            "revealing the viewed area to the provider. Choose Offline to "
            "avoid tile requests. Recent paths/settings remain on this computer.\n\n"
            "Folder import includes .txt files directly in that folder (no subfolders). "
            "Each file must have its own logger header and usable rows. An unusable file "
            "stops import with its filename; the previous session remains open.",
        )

    def dragEnterEvent(self, event):
        if any(
            url.isLocalFile()
            and (Path(url.toLocalFile()).is_dir() or url.toLocalFile().lower().endswith(".txt"))
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and (
                Path(url.toLocalFile()).is_dir() or url.toLocalFile().lower().endswith(".txt")
            ):
                self.load_file(url.toLocalFile())
                event.acceptProposedAction()
                break

    def closeEvent(self, event):
        if self.calculation_thread is not None:
            self.closing = True
            self.calculation_thread.requestInterruption()
            event.ignore()
            return
        if self.thread is not None:
            self.closing = True
            self.thread.requestInterruption()
            event.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("dock_state", self.saveState())
        event.accept()

    def open_later(self, path):
        QTimer.singleShot(0, lambda: self.load_file(path))
