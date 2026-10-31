"""Per-plot channel selection and explicit, fixed Y-axis limits."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from telemetry_viewer.processing.scales import channel_unit, full_y_range


class PlotConfigurationDialog(QDialog):
    def __init__(self, session, index, names, title, ranges, parent=None, tab=0):
        super().__init__(parent)
        self.session, self.initial_ranges = session, ranges
        self.setWindowTitle(f"Configure plot {index + 1}")
        self.resize(670, 610)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(title)
        self.title_edit.setMaxLength(128)
        form.addRow("Plot title", self.title_edit)
        layout.addLayout(form)
        self.tabs = QTabWidget()
        self.channel_tree = QTreeWidget()
        self.channel_tree.setHeaderLabels(["Channel", "Unit", "Source"])
        self.channel_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.channel_items = {}
        for name, channel in session.channels.items():
            item = QTreeWidgetItem(
                self.channel_tree, [name, channel.unit or "unspecified", channel.source]
            )
            item.setCheckState(
                0, Qt.CheckState.Checked if name in names else Qt.CheckState.Unchecked
            )
            self.channel_items[name] = item
        self.tabs.addTab(self.channel_tree, "Channels")
        ranges_page = QWidget()
        ranges_layout = QVBoxLayout(ranges_page)
        ranges_layout.addWidget(
            QLabel("Each unit has its own fixed Y scale. Mouse wheel and dragging change X only.")
        )
        self.range_table = QTableWidget(0, 4)
        self.range_table.setHorizontalHeaderLabels(["Unit", "Minimum", "Maximum", ""])
        self.range_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        ranges_layout.addWidget(self.range_table)
        self.tabs.addTab(ranges_page, "Y-axis ranges")
        self.range_fields = {}
        self.channel_tree.itemChanged.connect(self.rebuild_ranges)
        self.rebuild_ranges()
        self.tabs.setCurrentIndex(tab)
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_names(self):
        return [
            name
            for name, item in self.channel_items.items()
            if item.checkState(0) == Qt.CheckState.Checked
        ]

    def rebuild_ranges(self, *_):
        previous = {unit: (lo.value(), hi.value()) for unit, (lo, hi) in self.range_fields.items()}
        groups = {}
        for name in self.selected_names():
            channel = self.session.channels[name]
            groups.setdefault(channel_unit(channel), []).append(channel)
        self.range_fields.clear()
        self.range_table.setRowCount(len(groups))
        for row, (unit, channels) in enumerate(groups.items()):
            from PySide6.QtWidgets import QTableWidgetItem

            item = QTableWidgetItem(unit)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.range_table.setItem(row, 0, item)
            limits = previous.get(unit, self.initial_ranges.get(unit, full_y_range(channels)))
            fields = []
            for column, value in enumerate(limits, start=1):
                field = QDoubleSpinBox()
                field.setRange(-1e12, 1e12)
                field.setDecimals(6)
                field.setValue(value)
                self.range_table.setCellWidget(row, column, field)
                fields.append(field)
            self.range_fields[unit] = tuple(fields)
            fit = QPushButton("Fit to data")
            fit.clicked.connect(lambda _checked=False, u=unit, cs=channels: self.fit_unit(u, cs))
            self.range_table.setCellWidget(row, 3, fit)

    def fit_unit(self, unit, channels):
        low, high = full_y_range(channels)
        self.range_fields[unit][0].setValue(low)
        self.range_fields[unit][1].setValue(high)

    def ranges(self):
        return {unit: (lo.value(), hi.value()) for unit, (lo, hi) in self.range_fields.items()}

    def _accept(self):
        if any(lo >= hi for lo, hi in self.ranges().values()):
            QMessageBox.warning(
                self, "Invalid Y range", "Each minimum must be smaller than its maximum."
            )
            self.tabs.setCurrentIndex(1)
            return
        self.accept()
