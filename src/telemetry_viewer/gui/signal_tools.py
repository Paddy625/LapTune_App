"""Discoverable calculation/filter controls and full-resolution numerical analysis."""

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from telemetry_viewer.processing.signals import (
    OPERATIONS,
    UNIT_FACTORS,
    SignalRecipe,
    channel_statistics,
    normalized_unit,
    validate_recipe,
)


def number(value, low=-1e12, high=1e12):
    field = QDoubleSpinBox()
    field.setDecimals(6)
    field.setRange(low, high)
    field.setValue(value)
    return field


class SignalToolsDialog(QDialog):
    def __init__(self, session, parent=None, source=""):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Signal tools · calculated channels")
        self.resize(740, 660)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.operation = QComboBox()
        for key, label in OPERATIONS.items():
            self.operation.addItem(label, key)
        form.addRow("Operation", self.operation)
        self.sources = []
        for index, label in enumerate(("Source A", "Source B", "Source C (optional)")):
            combo = QComboBox()
            if index == 2:
                combo.addItem("None · use two axes", "")
            for name, channel in session.channels.items():
                combo.addItem(f"{name} [{channel.unit}]", name)
            self.sources.append(combo)
            form.addRow(label, combo)
        self.sources[0].setCurrentIndex(max(0, self.sources[0].findData(source or "ACC_X")))
        if "ACC_Y" in session.channels:
            self.sources[1].setCurrentIndex(self.sources[1].findData("ACC_Y"))
        self.source_info = QLabel()
        self.source_info.setWordWrap(True)
        form.addRow(self.source_info)
        self.cutoff = number(5, 0.000001, 100000)
        self.cutoff.setSuffix(" Hz")
        form.addRow("Cutoff frequency", self.cutoff)
        self.zero_phase = QCheckBox("Forward/backward · zero phase, stronger smoothing")
        self.zero_phase.setChecked(True)
        form.addRow(self.zero_phase)
        self.max_gap = number(1, 0.000001, 100000)
        self.max_gap.setSuffix(" s")
        form.addRow("Reset state after a gap longer than", self.max_gap)
        self.gain, self.offset = number(1), number(0)
        form.addRow("Gain", self.gain)
        form.addRow("Offset (in output units)", self.offset)
        self.unit = QComboBox()
        self.unit.setEditable(True)
        self.unit.addItems(list(UNIT_FACTORS))
        form.addRow("Output unit (conversion / gain)", self.unit)
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        form.addRow("New channel name", self.name)
        layout.addLayout(form)
        note = QLabel(
            "Creates a channel in Calculated; select it in Configure plot… to display it.\n"
            "Low-pass retains gravity and sustained load; high-pass removes sustained load too.\n"
            "RC filtering uses actual time intervals; invalid samples and long gaps reset state.\n"
            "Forward/backward: edge transients, approximately −6 dB at the RC cutoff.\n"
            "Multiple sources use shared exact measurement times, without filling missing samples."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create channel")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._auto_name = ""
        self.operation.currentIndexChanged.connect(self.update_fields)
        self.sources[0].currentIndexChanged.connect(self.update_fields)
        self.update_fields()

    def update_fields(self, *_):
        operation = self.operation.currentData()
        source = self.session.channels[self.sources[0].currentData()]
        self.sources[1].setEnabled(operation in ("magnitude", "difference"))
        self.sources[2].setEnabled(operation == "magnitude")
        filtering = operation in ("lowpass", "highpass")
        self.cutoff.setEnabled(filtering)
        self.zero_phase.setEnabled(filtering)
        self.max_gap.setEnabled(filtering or operation in ("derivative", "integral"))
        self.gain.setEnabled(operation == "scale")
        self.offset.setEnabled(operation == "scale")
        self.unit.setEnabled(operation in ("convert", "scale"))
        base = normalized_unit(source.unit)
        defaults = {
            "g": "m/s²",
            "m/s²": "g",
            "km/h": "m/s",
            "m/s": "km/h",
            "deg/s": "rad/s",
            "rad/s": "deg/s",
        }
        self.unit.setCurrentText(
            defaults.get(base, source.unit) if operation == "convert" else source.unit
        )
        differences = np.diff(source.time_s)
        positive = differences[differences > 0]
        spacing = f" · median recorded spacing {np.median(positive):.6g} s" if len(positive) else ""
        self.source_info.setText(f"Input unit: {source.unit or 'unspecified'}{spacing}")
        if not self.name.text() or self.name.text() == self._auto_name:
            stem = f"CALC_{source.name}_{operation.upper()}"
            candidate, suffix = stem[:76], 2
            while candidate in self.session.channels:
                candidate = f"{stem[:72]}_{suffix}"
                suffix += 1
            self._auto_name = candidate
            self.name.setText(candidate)

    def recipe(self):
        operation = self.operation.currentData()
        sources = [self.sources[0].currentData()]
        if operation in ("magnitude", "difference"):
            sources.append(self.sources[1].currentData())
        if operation == "magnitude" and self.sources[2].currentData():
            sources.append(self.sources[2].currentData())
        return SignalRecipe(
            self.name.text().strip(),
            operation,
            tuple(sources),
            self.unit.currentText().strip(),
            self.gain.value(),
            self.offset.value(),
            self.cutoff.value(),
            self.zero_phase.isChecked(),
            self.max_gap.value(),
        )

    def _accept(self):
        try:
            validate_recipe(self.session.channels, self.recipe())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid calculation", str(exc))
            return
        self.accept()


class StatisticsDialog(QDialog):
    def __init__(self, session, mode, bounds, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Signal analysis · full-resolution samples")
        self.resize(1000, 560)
        self.session, self.mode, self.bounds = session, mode, bounds
        layout = QVBoxLayout(self)
        self.scope = QComboBox()
        self.scope.addItems(["Visible X range", "Entire session"])
        layout.addWidget(self.scope)
        self.table = QTreeWidget()
        self.table.setHeaderLabels(
            ["Channel", "Unit", "Samples", "Min", "Max", "Mean", "RMS", "Std dev", "Peak to peak"]
        )
        self.table.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        note = QLabel(
            "Valid full-resolution samples only; display downsampling never affects statistics."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.scope.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        self.table.clear()
        bounds = self.bounds if self.scope.currentIndex() == 0 else None
        for channel in self.session.channels.values():
            values = channel_statistics(
                channel, self.session.channel_axis(channel, self.mode), bounds
            )
            row = [channel.name, channel.unit, str(values["count"])]
            row += [
                f"{values[key]:.6g}" if values[key] is not None else "—"
                for key in ("min", "max", "mean", "rms", "std", "peak_to_peak")
            ]
            item = QTreeWidgetItem(self.table, row)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight)
