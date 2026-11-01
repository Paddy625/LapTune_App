import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsProxyWidget,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from telemetry_viewer.domain.models import Session
from telemetry_viewer.gui.theme import BACKGROUND, CURSOR, MUTED, SERIES_COLORS, SURFACE, TEXT
from telemetry_viewer.processing.display import elapsed_clock
from telemetry_viewer.processing.scales import channel_unit, full_y_range

COLORS = SERIES_COLORS


class CachedAxis(pg.AxisItem):
    """Rasterize axis pictures without Qt's item-cache clipping of sibling axes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._raster = None
        self._raster_picture = None
        self._raster_bounds = None
        self._raster_ratio = None

    def paint(self, painter, option, widget):
        if self.orientation in ("left", "right"):
            painter.save()
            try:
                super().paint(painter, option, widget)
            finally:
                painter.restore()
            return
        drawn = False
        if self.picture is None:
            # Let AxisItem initialize and paint on the live viewport before caching.
            # Painting into an offscreen cache first can clip sibling Y labels.
            painter.save()
            try:
                super().paint(painter, option, widget)
            finally:
                painter.restore()
            drawn = True
        bounds = self.boundingRect().toAlignedRect()
        ratio = painter.device().devicePixelRatioF()
        if (
            self.picture is None
            or self._raster_picture is not self.picture
            or bounds != self._raster_bounds
            or ratio != self._raster_ratio
        ):
            raster = QPixmap(
                max(1, int(np.ceil(bounds.width() * ratio))),
                max(1, int(np.ceil(bounds.height() * ratio))),
            )
            raster.setDevicePixelRatio(ratio)
            raster.fill(Qt.GlobalColor.transparent)
            target = QPainter(raster)
            target.setRenderHints(painter.renderHints())
            target.translate(-bounds.left(), -bounds.top())
            try:
                self.picture.play(target)
            finally:
                target.end()
            self._raster, self._raster_picture = raster, self.picture
            self._raster_bounds, self._raster_ratio = bounds, ratio
        if not drawn:
            painter.drawPixmap(bounds.topLeft(), self._raster)


class TimeAxis(CachedAxis):
    clock_format = True

    def boundingRect(self):
        # hh:mm:ss labels need room on both edges, including the zero-time tick.
        return super().boundingRect().adjusted(-30, 0, 30, 0)

    def tickSpacing(self, minVal, maxVal, size):
        if not self.clock_format:
            return super().tickSpacing(minVal, maxVal, size)
        duration = abs(maxVal - minVal)
        if duration == 0:
            return []
        label = elapsed_clock(max(abs(minVal), abs(maxVal)))
        label_width = QFontMetricsF(self.font()).horizontalAdvance(label) + 16
        minimum = max(1e-12, duration * label_width / max(size, 1))
        base = 10 ** math.floor(math.log10(minimum))
        spacing = next(
            multiplier * base for multiplier in (1, 2, 5, 10) if multiplier * base >= minimum
        )
        if minimum >= 1:
            friendly = (
                1,
                2,
                5,
                10,
                15,
                30,
                60,
                120,
                300,
                600,
                900,
                1800,
                3600,
                7200,
                21600,
                43200,
                86400,
            )
            spacing = next((value for value in friendly if value >= minimum), spacing)
        # Only one label level: minor numeric ticks would overlap long clock labels.
        return [(spacing, 0)]

    def tickStrings(self, values, scale, spacing):
        if not self.clock_format:
            return super().tickStrings(values, scale, spacing)
        # Avoid repeated hh:mm:ss labels when zoomed to subsecond intervals.
        seen, labels = set(), []
        for value in values:
            label = elapsed_clock(float(value))
            labels.append(label if label not in seen else "")
            seen.add(label)
        return labels


class XOnlyViewBox(pg.ViewBox):
    def __init__(self, options_menu=None, x_master=None):
        super().__init__()
        self.options_menu, self.x_master = options_menu, x_master
        self.setMouseEnabled(x=True, y=False)
        self.enableAutoRange(x=False, y=False)

    def wheelEvent(self, event, axis=None):
        # Also enforce X-only wheel behavior when an AxisItem forwards axis=1.
        target = self.x_master if self.x_master is not None else self
        pg.ViewBox.wheelEvent(target, event, axis=0)

    def mouseDragEvent(self, event, axis=None):
        target = self.x_master if self.x_master is not None else self
        pg.ViewBox.mouseDragEvent(target, event, axis=0)

    def raiseContextMenu(self, event):
        if self.options_menu is not None:
            self.options_menu.exec(event.screenPos().toPoint())
            event.accept()


class PlotPanel(QWidget):
    position_changed = Signal(float)
    configure_requested = Signal(int, int)
    layout_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        pg.setConfigOptions(antialias=False, background=BACKGROUND, foreground=MUTED)
        self.session: Session | None = None
        self.mode = "time"
        self.curves, self.proxies = {}, []
        self._curve_views, self._masked_values = {}, {}
        self.unit_views = [{} for _ in range(4)]
        self.unit_axes = [{} for _ in range(4)]
        self._extras = [[] for _ in range(4)]
        self._aux_cursors = [[] for _ in range(4)]
        self.y_ranges = [{} for _ in range(4)]
        self.manual_ranges = [{} for _ in range(4)]
        self.titles = [f"Plot {i + 1}" for i in range(4)]
        self._unit_channels = [{} for _ in range(4)]
        self._right_columns = 0
        self.graphics = pg.GraphicsLayoutWidget()
        # Clock labels extend beyond the last tick; keep their full hh:mm:ss visible.
        self.graphics.ci.layout.setContentsMargins(9, 9, 40, 9)
        self.plots, self.cursors, self.axes = [], [], []
        self.headers, self.title_labels, self.options_menus = [], [], []
        for i in range(4):
            menu = QMenu(self)
            menu.addAction(
                "Configure channels…",
                lambda _checked=False, p=i: self.configure_requested.emit(p, 0),
            )
            menu.addAction(
                "Y-axis ranges…", lambda _checked=False, p=i: self.configure_requested.emit(p, 1)
            )
            menu.addAction("Fit Y to all data", lambda _checked=False, p=i: self.fit_y(p))
            menu.addAction("Fit X to session", self.fit_all)
            self.options_menus.append(menu)
            header = QWidget()
            header.setStyleSheet(f"background: {SURFACE};")
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(8, 0, 4, 0)
            label = QLabel(self.titles[i])
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            header_layout.addWidget(label, 1)
            button = QToolButton()
            button.setObjectName(f"configure_plot_{i + 1}")
            button.setAccessibleName(f"Configure plot {i + 1}")
            button.setText("Configure plot…")
            button.setMenu(menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            button.clicked.connect(lambda _checked=False, p=i: self.configure_requested.emit(p, 0))
            header_layout.addWidget(button)
            proxy = QGraphicsProxyWidget()
            proxy.setWidget(header)
            self.graphics.addItem(proxy, row=2 * i, col=0)
            self.headers.append(proxy)
            self.title_labels.append(label)
            self.graphics.ci.layout.setRowFixedHeight(2 * i, 32)
            axis = TimeAxis(orientation="bottom")
            view = XOnlyViewBox(menu)
            plot = self.graphics.addPlot(
                row=2 * i + 1,
                col=0,
                viewBox=view,
                axisItems={
                    "bottom": axis,
                    "left": CachedAxis("left"),
                    "right": CachedAxis("right"),
                },
            )
            plot.setDownsampling(auto=False, mode="peak")
            plot.showGrid(x=True, y=False, alpha=0.12)
            # Keep horizontal grid rendering separate from Y labels. Its bitmap can
            # be cached safely; cursor paints then need not replay vertical-axis text.
            grid = pg.GridItem(pen=pg.mkPen((154, 160, 169, 35)), textPen=None)
            grid.setTickSpacing(x=[1e100], y=[None, None])
            grid.setZValue(-20)
            grid.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
            plot.addItem(grid, ignoreBounds=True)
            plot.hideButtons()
            self._style_axis(plot.getAxis("left"))
            plot.getAxis("left").setWidth(78)
            self._style_axis(axis)
            axis.enableAutoSIPrefix(False)
            axis.setStyle(hideOverlappingLabels=False)
            plot.addLegend(offset=(10, 5), labelTextColor=TEXT)
            plot.setClipToView(True)
            cursor = pg.InfiniteLine(
                angle=90,
                movable=False,
                pen=pg.mkPen(CURSOR, width=1, style=Qt.PenStyle.DashLine),
            )
            cursor.setZValue(1000)
            plot.addItem(cursor, ignoreBounds=True)
            self.axes.append(axis)
            self.plots.append(plot)
            self.cursors.append(cursor)
            if i:
                plot.setXLink(self.plots[0])
            plot.vb.sigXRangeChanged.connect(lambda *_args, p=plot: self._update_sampling(p))
            plot.vb.sigResized.connect(lambda *_args, p=i: self._resize_views(p))
        scene = self.graphics.scene()
        self.proxies.append(pg.SignalProxy(scene.sigMouseMoved, rateLimit=30, slot=self._hover))
        scene.sigMouseClicked.connect(self._click)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.graphics)
        self.set_count(3)

    @staticmethod
    def _style_axis(axis):
        axis.enableAutoSIPrefix(False)
        # The axis owns its raster cache; Qt item caches can clip other axes in a scene.
        axis.setCacheMode(QGraphicsItem.CacheMode.NoCache)

    def _hover(self, args):
        for plot in self.plots:
            if plot.isVisible() and plot.vb.sceneBoundingRect().contains(args[0]):
                self.position_changed.emit(plot.vb.mapSceneToView(args[0]).x())
                break

    def _click(self, event):
        for plot in self.plots:
            if plot.isVisible() and plot.vb.sceneBoundingRect().contains(event.scenePos()):
                self.position_changed.emit(plot.vb.mapSceneToView(event.scenePos()).x())
                break

    def set_count(self, count):
        self._count = count
        for i, plot in enumerate(self.plots):
            plot.setVisible(i < count)
            self.headers[i].setVisible(i < count)
            self.graphics.ci.layout.setRowFixedHeight(2 * i, 32 if i < count else 0)
            for _unit, view, axis, _column in self._extras[i]:
                view.setVisible(i < count)
                axis.setVisible(i < count)
        self._align_axes()

    def _align_axes(self):
        columns = max((len(axes) - 1 for axes in self.unit_views[: self._count]), default=0)
        columns = max(0, columns)
        # Dock panels must yield space to the plot, including on small laptop screens.
        # Otherwise extra Y axes can end up outside the graphics viewport entirely.
        self.setMinimumWidth(max(360, 78 * (columns + 1) + 260))
        for plot in self.plots:
            for column in range(2, max(columns, self._right_columns) + 2):
                plot.layout.setColumnFixedWidth(column, 78 if column < columns + 2 else 0)
        self._right_columns = columns

    def _clear_curves(self):
        for key, (curve, plot) in self.curves.items():
            view = self._curve_views[key]
            if view is plot.vb:
                plot.removeItem(curve)
            else:
                view.removeItem(curve)
                plot.legend.removeItem(curve)
        self.curves.clear()
        self._curve_views.clear()

    def _create_unit_axes(self, panel, units):
        plot = self.plots[panel]
        # Reuse axes/viewboxes when only the selected channels or X mode changed.
        if list(self.unit_views[panel]) == units:
            return
        for _unit, view, axis, column in self._extras[panel]:
            axis.unlinkFromView()
            view.setXLink(None)
            if column == 2:
                axis.hide()
            else:
                plot.layout.removeItem(axis)
                plot.scene().removeItem(axis)
            plot.scene().removeItem(view)
        self._extras[panel], self._aux_cursors[panel] = [], []
        self.unit_views[panel], self.unit_axes[panel] = {}, {}
        plot.hideAxis("right")
        for index, unit in enumerate(units):
            if index == 0:
                view, axis = plot.vb, plot.getAxis("left")
            else:
                view = XOnlyViewBox(self.options_menus[panel], x_master=plot.vb)
                plot.scene().addItem(view)
                column = index + 1
                if index == 1:
                    axis = plot.getAxis("right")
                    plot.showAxis("right")
                else:
                    axis = CachedAxis("right")
                    plot.layout.addItem(axis, 2, column)
                self._style_axis(axis)
                axis.setWidth(78)
                axis.linkToView(view)
                view.setXLink(plot.vb)
                self._extras[panel].append((unit, view, axis, column))
                cursor = pg.InfiniteLine(
                    angle=90,
                    movable=False,
                    pen=pg.mkPen(CURSOR, width=1, style=Qt.PenStyle.DashLine),
                )
                cursor.setZValue(1000)
                view.addItem(cursor, ignoreBounds=True)
                self._aux_cursors[panel].append(cursor)
            axis.setLabel(unit)
            self.unit_views[panel][unit] = view
            self.unit_axes[panel][unit] = axis
        if not units:
            plot.getAxis("left").setLabel("No channels")
        self._resize_views(panel)

    def _resize_views(self, panel):
        plot = self.plots[panel]
        for _unit, view, _axis, _column in self._extras[panel]:
            view.setGeometry(plot.vb.sceneBoundingRect())
            view.linkedViewChanged(plot.vb, view.XAxis)
        for axis in set(self.unit_axes[panel].values()) | {self.axes[panel]}:
            axis.prepareGeometryChange()
            axis.picture = None
            axis.update()
        self._update_sampling(plot)

    def set_selection(self, session, selection, mode="time"):
        new_session = self.session is not session
        if new_session:
            self._masked_values.clear()
            self.y_ranges = [{} for _ in range(4)]
        self._clear_curves()
        self.session, self.mode = session, mode
        groups = [{} for _ in range(4)]
        for name, panel, color in selection:
            unit = channel_unit(session.channels[name])
            groups[panel].setdefault(unit, []).append((name, color))
        for i, plot in enumerate(self.plots):
            self.title_labels[i].setText(self.titles[i])
            self.title_labels[i].setToolTip(self.titles[i])
            self._create_unit_axes(i, list(groups[i]))
            self.axes[i].clock_format = mode == "time"
            self.axes[i].enableAutoSIPrefix(mode != "time")
            self.axes[i].picture = None
            self.axes[i].update()
            plot.setLabel(
                "bottom",
                "Elapsed time [hh:mm:ss]" if mode == "time" else "Distance",
                units=None if mode == "time" else "m",
            )
            for unit, entries in groups[i].items():
                names = [name for name, _ in entries]
                if unit not in self.y_ranges[i]:
                    self.y_ranges[i][unit] = self.manual_ranges[i].get(
                        unit, full_y_range([session.channels[name] for name in names])
                    )
                self.unit_axes[i][unit].setPen(entries[0][1])
                self.unit_axes[i][unit].setTextPen(entries[0][1])
                view = self.unit_views[i][unit]
                view.enableAutoRange(x=False, y=False)
                view.setYRange(*self.y_ranges[i][unit], padding=0)
            self._unit_channels[i] = {
                unit: [n for n, _ in entries] for unit, entries in groups[i].items()
            }
        # Reserve equal right-axis columns on every plot to keep X positions aligned.
        self._align_axes()
        for name, panel, color in selection:
            channel = session.channels[name]
            if name not in self._masked_values:
                finite = bool(channel.valid.all())
                y = channel.samples if finite else np.where(channel.valid, channel.samples, np.nan)
                self._masked_values[name] = y, finite
            y, finite = self._masked_values[name]
            plot = self.plots[panel]
            view = self.unit_views[panel][channel_unit(channel)]
            options = {"stepMode": "left"} if channel.interpolation == "hold" else {}
            curve = pg.PlotDataItem(
                session.channel_axis(channel, mode),
                y,
                name=f"{name} [{channel.unit}]",
                pen=pg.mkPen(color, width=1, cosmetic=True),
                connect="all" if finite else "finite",
                skipFiniteCheck=finite,
                autoDownsample=False,
                downsampleMethod="peak",
                **options,
            )
            if view is plot.vb:
                plot.addItem(curve)
            else:
                view.addItem(curve)
                plot.legend.addItem(curve, curve.name())
            curve.setClipToView(True)
            self.curves[(panel, name)] = curve, plot
            self._curve_views[(panel, name)] = view
        for i, plot in enumerate(self.plots):
            self._resize_views(i)
            self._update_sampling(plot)
        # Newly created auxiliary viewboxes must respect hidden panel visibility too.
        self.set_count(self._count)

    def _update_sampling(self, plot):
        if self.session is None:
            return
        left, right = plot.vb.viewRange()[0]
        budget = max(1, int(plot.vb.width() * 0.75))
        for curve, owner in self.curves.values():
            if owner is not plot:
                continue
            x, _y = curve.getOriginalDataset()
            if x is None or not len(x):
                continue
            lo = int(np.searchsorted(x, left, side="left"))
            hi = int(np.searchsorted(x, right, side="right"))
            curve.setDownsampling(
                ds=max(1, int(np.ceil((hi - lo) / budget))), auto=False, method="peak"
            )

    def set_y_ranges(self, panel, ranges):
        for unit, limits in ranges.items():
            self.manual_ranges[panel][unit] = tuple(limits)
            self.y_ranges[panel][unit] = tuple(limits)
            if unit in self.unit_views[panel]:
                self.unit_views[panel][unit].setYRange(*limits, padding=0)
        self.layout_changed.emit()

    def fit_y(self, panel):
        if self.session is None:
            return
        ranges = {
            unit: full_y_range([self.session.channels[n] for n in names])
            for unit, names in self._unit_channels[panel].items()
        }
        self.set_y_ranges(panel, ranges)

    def set_cursor(self, x):
        for cursor in self.cursors + [c for cursors in self._aux_cursors for c in cursors]:
            cursor.setPos(x)

    def fit_all(self):
        # Fit/reset affects X only. Y changes exclusively through the plot options.
        if self.session is not None:
            axis = self.session.axis(self.mode)
            self.plots[0].setXRange(float(axis[0]), max(float(axis[-1]), 0.001), padding=0.02)
