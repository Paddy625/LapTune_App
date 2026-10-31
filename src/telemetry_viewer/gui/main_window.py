import html
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolBar,
    QToolButton,
    QVBoxLayout,
)

from telemetry_viewer.exporters.files import export_csv, export_gpx
from telemetry_viewer.gui.channel_panel import ChannelPanel
from telemetry_viewer.gui.plot_panel import PlotPanel
from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter
from telemetry_viewer.maps.map_widget import MapWidget


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


class MainWindow(QMainWindow):
    session_loaded = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Telemetry Viewer")
        self.resize(1560, 920)
        self.setMinimumSize(1000, 600)
        self.settings = QSettings("LapTune", "TelemetryViewer")
        self.session = None
        self.source_path = None
        self.cursor_sample = 0
        self.thread = None
        self.closing = False
        self.plots = PlotPanel()
        self.setCentralWidget(self.plots)
        self.channels = ChannelPanel()
        self.channel_dock = QDockWidget("CHANNELS", self)
        self.channel_dock.setWidget(self.channels)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.channel_dock)
        self.map_widget = MapWidget()
        self.map_dock = QDockWidget("GPS ROUTE · nearest recorded fix", self)
        self.map_dock.setWidget(self.map_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.map_dock)
        self.resizeDocks([self.channel_dock, self.map_dock], [310, 430], Qt.Orientation.Horizontal)
        self.channels.selection_updated.connect(self.refresh_plots)
        self.plots.position_changed.connect(self.on_plot_position)
        self.map_widget.location_selected.connect(self.on_map_position)
        self._build_actions()
        self.summary = QLabel("Open a logger .TXT file to begin · all processing stays local")
        self.summary.setWordWrap(True)
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
        self.layer_combo.setCurrentText(self.settings.value("map_layer", "Road"))

    def _build_actions(self):
        self.channel_dock.setObjectName("channels")
        self.map_dock.setObjectName("gps_map")
        file_menu = self.menuBar().addMenu("&File")
        self.open_action = QAction("Open…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.choose_file)
        file_menu.addAction(self.open_action)
        self.recent_menu = file_menu.addMenu("Recent files")
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
        view.addAction("Fit all", self.plots.fit_all, "Ctrl+0")
        session_menu = self.menuBar().addMenu("&Session")
        session_menu.addAction("Metadata and map provider…", self.configure)
        session_menu.addAction("Data quality and distance report…", self.show_quality)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("Controls and privacy", self.show_help)
        toolbar = QToolBar("Analysis")
        toolbar.setObjectName("analysis")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addAction(self.open_action)
        recent = QToolButton()
        recent.setText("Recent")
        recent.setMenu(self.recent_menu)
        recent.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(recent)
        toolbar.addSeparator()
        self.axis_combo = QComboBox()
        self.axis_combo.addItems(["Time", "Distance"])
        self.axis_combo.currentIndexChanged.connect(self.axis_changed)
        toolbar.addWidget(self.axis_combo)
        self.distance_combo = QComboBox()
        self.distance_combo.addItems(["Speed integral", "GPS geodesic"])
        self.distance_combo.setToolTip("Distance method; compare totals in Session > Data quality")
        self.distance_combo.currentIndexChanged.connect(self.distance_changed)
        toolbar.addWidget(self.distance_combo)
        self.time_format = QComboBox()
        self.time_format.addItems(["Seconds", "mm:ss.sss"])
        self.time_format.currentIndexChanged.connect(self.refresh_plots)
        toolbar.addWidget(self.time_format)
        toolbar.addAction("Fit all", self.plots.fit_all)
        toolbar.addAction("Reset zoom", self.plots.fit_all)
        self.count_combo = QComboBox()
        self.count_combo.addItems(["1 plot", "2 plots", "3 plots", "4 plots"])
        self.count_combo.setCurrentIndex(2)
        self.count_combo.currentIndexChanged.connect(lambda i: self.plots.set_count(i + 1))
        toolbar.addWidget(self.count_combo)
        toolbar.addSeparator()
        self.layer_combo = QComboBox()
        self.layer_combo.addItems(["Road", "Offline", "Satellite"])
        self.layer_combo.currentTextChanged.connect(self.layer_changed)
        toolbar.addWidget(self.layer_combo)
        toolbar.addAction("Settings", self.configure)
        export = QToolButton()
        export.setText("Export")
        export.setMenu(self.export_menu)
        export.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(export)
        toolbar.addAction("Quality report", self.show_quality)

    def _refresh_recent(self):
        self.recent_menu.clear()
        for path in self.settings.value("recent", [], type=list):
            self.recent_menu.addAction(Path(path).name, lambda p=path: self.load_file(p))
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

    def load_file(self, path):
        if self.thread is not None:
            return
        self.pending_path = Path(path).resolve()
        self.open_action.setEnabled(False)
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
        self.channels.populate(session)
        self.map_widget.set_session(session)
        self.plots.fit_all()
        self.set_cursor(0)
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
        required = max([panel + 1 for _, panel, _ in selection], default=1)
        if self.count_combo.currentIndex() + 1 < required:
            self.count_combo.setCurrentIndex(required - 1)
        self.plots.set_selection(
            self.session, selection, self.axis_mode, self.time_format.currentIndex() == 1
        )
        self.set_cursor(self.cursor_sample)

    @property
    def axis_mode(self):
        return "time" if self.axis_combo.currentIndex() == 0 else "distance"

    def axis_changed(self, *_):
        self.refresh_plots()
        self.plots.fit_all()

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
            f"Row {i:,} · NR {s.nr[i]} · {s.elapsed_time_s[i]:.3f} s · {s.distance_m[i]:.1f} m"
        )

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
                self.apply_units(fields["acc_unit"].text(), fields["gyro_unit"].text())
                self.channels.populate(self.session)
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
        table = QTableWidget(len(s.issues), 3)
        table.setHorizontalHeaderLabels(["File line", "Code", "Diagnostic"])
        for row, issue in enumerate(s.issues):
            for col, text in enumerate([str(issue.line), issue.code, issue.message]):
                table.setItem(row, col, QTableWidgetItem(text))
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def export(self, kind):
        if self.session is None:
            return
        names = [name for name, _, _ in self.channels.selection()]
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
        if self.source_path and (
            target.resolve() == self.source_path
            or target.with_suffix(target.suffix + ".metadata.json").resolve() == self.source_path
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

    def show_help(self):
        QMessageBox.information(
            self,
            "Controls and privacy",
            "Ctrl+O or drop a TXT file to import. Wheel: zoom; drag: pan; "
            "right-drag: rectangle zoom; Ctrl+0: fit all. Hover/click plots "
            "to move cursor. Click the map to select the nearest GPS change; "
            "repeated passages prefer the active time.\n\n"
            "Channel context menu: solo/hide all. Panels share X range. "
            "Distance plateaus preserve the active time where possible.\n\n"
            "Logs remain local. Online maps request tiles around the route, "
            "revealing the viewed area to the provider. Choose Offline to "
            "avoid tile requests. Recent paths/settings remain on this computer.",
        )

    def dragEnterEvent(self, event):
        if any(
            url.isLocalFile() and url.toLocalFile().lower().endswith(".txt")
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".txt"):
                self.load_file(url.toLocalFile())
                event.acceptProposedAction()
                break

    def closeEvent(self, event):
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
