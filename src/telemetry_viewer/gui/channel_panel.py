import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
)

from telemetry_viewer.gui.plot_panel import COLORS


class ChannelPanel(QTreeWidget):
    selection_updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Channel / unit", "Panel", "Value"])
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(0, 245)
        self.setColumnWidth(2, 75)
        self.header().setMinimumSectionSize(55)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.items = {}
        self.session = None
        self.itemChanged.connect(
            lambda item, column: self.selection_updated.emit() if column == 0 else None
        )
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def populate(self, session):
        self.blockSignals(True)
        self.clear()
        self.session, self.items = session, {}
        groups = {}
        for i, (name, channel) in enumerate(session.channels.items()):
            if channel.group not in groups:
                groups[channel.group] = QTreeWidgetItem(self, [channel.group])
            item = QTreeWidgetItem(groups[channel.group], [f"{name} [{channel.unit}]", "", "—"])
            item.setSizeHint(0, QSize(0, 34))
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            item.setCheckState(
                0,
                Qt.CheckState.Checked
                if name in ("GPS_SPEED_KMH", "ACC_X", "ACC_Y", "GYRO_Z")
                else Qt.CheckState.Unchecked,
            )
            color = COLORS[i % len(COLORS)]
            item.setForeground(0, QColor(color))
            item.setToolTip(
                0, f"{channel.source}; recorded samples; no fixed update-rate assumption"
            )
            panel = QComboBox()
            panel.addItems(["1", "2", "3", "4"])
            panel.setCurrentIndex(
                {"GPS": 0, "Acceleration": 1, "Gyroscope": 2}.get(channel.group, 3)
            )
            panel.currentIndexChanged.connect(lambda *_: self.selection_updated.emit())
            self.setItemWidget(item, 1, panel)
            self.items[name] = item, panel, color
        self.expandAll()
        self.blockSignals(False)
        self.selection_updated.emit()

    def selection(self):
        return [
            (name, panel.currentIndex(), color)
            for name, (item, panel, color) in self.items.items()
            if item.checkState(0) == Qt.CheckState.Checked
        ]

    def set_cursor(self, sample):
        if self.session is None:
            return
        time = self.session.elapsed_time_s[sample]
        for name, (item, _, _) in self.items.items():
            channel = self.session.channels[name]
            i = min(int(np.searchsorted(channel.time_s, time)), len(channel.time_s) - 1)
            if i and abs(channel.time_s[i - 1] - time) <= abs(channel.time_s[i] - time):
                i -= 1
            item.setText(2, f"{channel.samples[i]:.3f}" if channel.valid[i] else "invalid")
            item.setToolTip(2, f"Nearest recorded sample at {channel.time_s[i]:.6f} s")

    def _menu(self, pos):
        target = self.itemAt(pos)
        if target is None or not target.data(0, Qt.ItemDataRole.UserRole):
            return
        menu = QMenu(self)
        only = menu.addAction("Show only this channel")
        none = menu.addAction("Hide all channels")
        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen in (only, none):
            self.blockSignals(True)
            for item, _, _ in self.items.values():
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked
                    if chosen == only and item is target
                    else Qt.CheckState.Unchecked,
                )
            self.blockSignals(False)
            self.selection_updated.emit()
