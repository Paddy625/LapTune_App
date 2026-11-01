import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
)

from telemetry_viewer.gui.plot_panel import COLORS


class ChannelPanel(QTreeWidget):
    selection_updated = Signal()
    calculate_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Channel / unit", "Value"])
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(0, 245)
        self.header().setMinimumSectionSize(55)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.items = {}
        self.session = None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def populate(self, session, assignments=None):
        self.blockSignals(True)
        self.clear()
        self.session, self.items = session, {}
        groups = {}
        for i, (name, channel) in enumerate(session.channels.items()):
            if channel.group not in groups:
                groups[channel.group] = QTreeWidgetItem(self, [channel.group])
            item = QTreeWidgetItem(groups[channel.group], [f"{name} [{channel.unit}]", "—"])
            item.setSizeHint(0, QSize(0, 34))
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            default_panel = {"GPS": 0, "Acceleration": 1, "Gyroscope": 2}.get(channel.group, 3)
            selected = name in ("GPS_SPEED_KMH", "ACC_X", "ACC_Y", "GYRO_Z")
            panels = [default_panel] if selected else []
            if assignments is not None:
                panels = [p for p, names in enumerate(assignments) if name in names]
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            color = COLORS[i % len(COLORS)]
            item.setForeground(0, QColor(color))
            item.setToolTip(
                0, f"{channel.source}; recorded samples; no fixed update-rate assumption"
            )
            self.items[name] = item, set(panels), color
        self.expandAll()
        self.blockSignals(False)
        self.selection_updated.emit()

    def selection(self):
        return [
            (name, panel, color)
            for name, (_item, panels, color) in self.items.items()
            for panel in sorted(panels)
        ]

    def apply_plot_selection(self, panel, names):
        self.blockSignals(True)
        for name, (_item, panels, _color) in self.items.items():
            panels.discard(panel)
            if name in names:
                panels.add(panel)
        self.blockSignals(False)
        self.selection_updated.emit()

    def set_cursor(self, sample):
        if self.session is None:
            return
        time = self.session.elapsed_time_s[sample]
        for name, (item, _, _) in self.items.items():
            channel = self.session.channels[name]
            if channel.time_s is self.session.elapsed_time_s:
                i = sample
            else:
                i = min(int(np.searchsorted(channel.time_s, time)), len(channel.time_s) - 1)
                if i and abs(channel.time_s[i - 1] - time) <= abs(channel.time_s[i] - time):
                    i -= 1
            item.setText(1, f"{channel.samples[i]:.3f}" if channel.valid[i] else "invalid")
            item.setToolTip(1, f"Nearest sample at {channel.time_s[i]:.6f} s")

    def _menu(self, pos):
        target = self.itemAt(pos)
        if target is None or not target.data(0, Qt.ItemDataRole.UserRole):
            return
        menu = QMenu(self)
        calculate = menu.addAction("Calculate / filter…")
        only = menu.addAction("Show only this channel")
        none = menu.addAction("Hide all channels")
        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen == calculate:
            self.calculate_requested.emit(target.data(0, Qt.ItemDataRole.UserRole))
            return
        if chosen in (only, none):
            for item, panels, _ in self.items.values():
                if chosen == none or item is not target:
                    panels.clear()
            if chosen == only:
                name = target.data(0, Qt.ItemDataRole.UserRole)
                panels = self.items[name][1]
                if not panels:
                    channel = self.session.channels[name]
                    panels.add({"GPS": 0, "Acceleration": 1, "Gyroscope": 2}.get(channel.group, 3))
            self.selection_updated.emit()
