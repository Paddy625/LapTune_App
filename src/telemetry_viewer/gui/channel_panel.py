import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
)

from telemetry_viewer.gui.plot_panel import COLORS


class PanelSelector(QToolButton):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        menu = QMenu(self)
        self.actions_by_panel = []
        for i in range(4):
            action = menu.addAction(f"Plot {i + 1}")
            action.setCheckable(True)
            action.toggled.connect(self._changed)
            self.actions_by_panel.append(action)
        self.setMenu(menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.set_panels([])

    def panels(self):
        return [i for i, a in enumerate(self.actions_by_panel) if a.isChecked()]

    def set_panels(self, panels):
        for i, action in enumerate(self.actions_by_panel):
            action.blockSignals(True)
            action.setChecked(i in panels)
            action.blockSignals(False)
        self.setText(", ".join(str(i + 1) for i in self.panels()) or "—")

    def _changed(self, *_):
        self.setText(", ".join(str(i + 1) for i in self.panels()) or "—")
        self.changed.emit()


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
        self.itemChanged.connect(self._item_changed)
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
            item = QTreeWidgetItem(groups[channel.group], [f"{name} [{channel.unit}]", "", "—"])
            item.setSizeHint(0, QSize(0, 34))
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            default_panel = {"GPS": 0, "Acceleration": 1, "Gyroscope": 2}.get(channel.group, 3)
            selected = name in ("GPS_SPEED_KMH", "ACC_X", "ACC_Y", "GYRO_Z")
            panels = [default_panel] if selected else []
            if assignments is not None:
                panels = [p for p, names in enumerate(assignments) if name in names]
            item.setCheckState(0, Qt.CheckState.Checked if panels else Qt.CheckState.Unchecked)
            color = COLORS[i % len(COLORS)]
            item.setForeground(0, QColor(color))
            item.setToolTip(
                0, f"{channel.source}; recorded samples; no fixed update-rate assumption"
            )
            panel = PanelSelector()
            panel.set_panels(panels)
            panel.changed.connect(lambda n=name: self._panels_changed(n))
            self.setItemWidget(item, 1, panel)
            self.items[name] = item, panel, color
        self.expandAll()
        self.blockSignals(False)
        self.selection_updated.emit()

    def _item_changed(self, item, column):
        if column != 0:
            return
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if name not in self.items:
            return
        selector = self.items[name][1]
        if item.checkState(0) == Qt.CheckState.Checked and not selector.panels():
            channel = self.session.channels[name]
            selector.set_panels(
                [{"GPS": 0, "Acceleration": 1, "Gyroscope": 2}.get(channel.group, 3)]
            )
        self.selection_updated.emit()

    def _panels_changed(self, name):
        item, selector, _color = self.items[name]
        self.blockSignals(True)
        item.setCheckState(
            0, Qt.CheckState.Checked if selector.panels() else Qt.CheckState.Unchecked
        )
        self.blockSignals(False)
        self.selection_updated.emit()

    def selection(self):
        return [
            (name, panel, color)
            for name, (item, selector, color) in self.items.items()
            if item.checkState(0) == Qt.CheckState.Checked
            for panel in selector.panels()
        ]

    def apply_plot_selection(self, panel, names):
        self.blockSignals(True)
        for name, (item, selector, _color) in self.items.items():
            panels = (
                set(selector.panels()) if item.checkState(0) == Qt.CheckState.Checked else set()
            )
            panels.discard(panel)
            if name in names:
                panels.add(panel)
            selector.set_panels(panels)
            item.setCheckState(0, Qt.CheckState.Checked if panels else Qt.CheckState.Unchecked)
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
            if chosen == only:
                name = target.data(0, Qt.ItemDataRole.UserRole)
                selector = self.items[name][1]
                if not selector.panels():
                    channel = self.session.channels[name]
                    selector.set_panels(
                        [{"GPS": 0, "Acceleration": 1, "Gyroscope": 2}.get(channel.group, 3)]
                    )
            self.selection_updated.emit()
