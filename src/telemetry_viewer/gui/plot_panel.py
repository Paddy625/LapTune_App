import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from telemetry_viewer.domain.models import Session

COLORS = ["#42d4c3", "#ffa65c", "#a895f8", "#6bbcf7", "#ff7897", "#d2dc73", "#f6ce77"]


class TimeAxis(pg.AxisItem):
    clock_format = False

    def tickStrings(self, values, scale, spacing):
        if not self.clock_format:
            return super().tickStrings(values, scale, spacing)
        return [f"{int(v // 60):02d}:{v % 60:06.3f}" for v in values]


class PlotPanel(QWidget):
    position_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        pg.setConfigOptions(antialias=False, background="#101a25", foreground="#9eafc2")
        self.session: Session | None = None
        self.mode = "time"
        self.curves, self.proxies = {}, []
        self.graphics = pg.GraphicsLayoutWidget()
        self.plots, self.cursors, self.axes = [], [], []
        for i in range(4):
            axis = TimeAxis(orientation="bottom")
            plot = self.graphics.addPlot(row=i, col=0, axisItems={"bottom": axis})
            plot.showGrid(x=True, y=True, alpha=0.12)
            plot.setLabel("left", f"Panel {i + 1}")
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.addLegend(offset=(10, 5), labelTextColor="#d5e1ef")
            plot.setMouseEnabled(x=True, y=True)
            plot.setClipToView(True)
            cursor = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#f9cf75", width=1))
            cursor.setZValue(20)
            plot.addItem(cursor, ignoreBounds=True)
            self.axes.append(axis)
            self.plots.append(plot)
            self.cursors.append(cursor)
            if i:
                plot.setXLink(self.plots[0])
            self.proxies.append(
                pg.SignalProxy(
                    plot.scene().sigMouseMoved,
                    rateLimit=30,
                    slot=lambda args, p=plot: self._hover(p, args),
                )
            )
            plot.scene().sigMouseClicked.connect(lambda event, p=plot: self._click(p, event))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.graphics)
        self.set_count(3)

    def _hover(self, plot, args):
        if plot.isVisible() and plot.sceneBoundingRect().contains(args[0]):
            self.position_changed.emit(plot.vb.mapSceneToView(args[0]).x())

    def _click(self, plot, event):
        if plot.isVisible() and plot.sceneBoundingRect().contains(event.scenePos()):
            self.position_changed.emit(plot.vb.mapSceneToView(event.scenePos()).x())

    def set_count(self, count):
        for i, plot in enumerate(self.plots):
            plot.setVisible(i < count)

    def set_selection(self, session, selection, mode="time", clock=False):
        self.session, self.mode = session, mode
        for curve, plot in self.curves.values():
            plot.removeItem(curve)
        self.curves.clear()
        for axis in self.axes:
            axis.clock_format = clock and mode == "time"
            axis.picture = None
            axis.update()
        for plot in self.plots:
            plot.setLabel(
                "bottom",
                "Elapsed time" if mode == "time" else "Distance",
                units="s" if mode == "time" else "m",
            )
        for panel, plot in enumerate(self.plots):
            units = {session.channels[n].unit for n, p, _ in selection if p == panel}
            label = next(iter(units)) if len(units) == 1 else "mixed units"
            plot.setLabel("left", f"Panel {panel + 1} · {label}")
        for name, panel, color in selection:
            channel = session.channels[name]
            y = np.where(channel.valid, channel.samples, np.nan)
            plot = self.plots[panel]
            options = {"stepMode": "left"} if channel.interpolation == "hold" else {}
            curve = plot.plot(
                session.channel_axis(channel, mode),
                y,
                name=f"{name} [{channel.unit}]",
                pen=pg.mkPen(color, width=1.3),
                connect="finite",
                autoDownsample=True,
                downsampleMethod="peak",
                **options,
            )
            curve.setClipToView(True)
            self.curves[name] = curve, plot

    def set_cursor(self, x):
        for cursor in self.cursors:
            cursor.setPos(x)

    def fit_all(self):
        for plot in self.plots:
            plot.enableAutoRange()
        if self.session is not None:
            axis = self.session.axis(self.mode)
            self.plots[0].setXRange(float(axis[0]), max(float(axis[-1]), 0.001), padding=0.02)
