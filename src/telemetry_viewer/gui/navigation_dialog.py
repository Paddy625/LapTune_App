"""Driving calibration and navigation settings; every published signal is explicitly CALC."""

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from telemetry_viewer.processing.navigation import NavigationConfig
from telemetry_viewer.processing.rotations import VEHICLE_AXES


class NavigationDialog(QDialog):
    def __init__(self, session, mode, bounds, parent=None):
        super().__init__(parent)
        self.session, self.mode, self.bounds = session, mode, bounds
        self.setWindowTitle("Estimate vehicle motion · CALC")
        self.resize(670, 640)
        root = QVBoxLayout(self)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        root.addWidget(scroll)
        intro = QLabel(
            "Calibrate the mounting from forward driving, then combine ACC/GYRO with GPS. "
            f"Vehicle axes: {VEHICLE_AXES}. No stationary calibration is used.\n\n"
            "All outputs are named CALC_… in Calculated. Original signals keep their "
            "names and values.\n\n"
            "CALC_ACC is vehicle acceleration in m/s². CALC_G_LOAD is the opposite "
            "motion-induced inertial load in g; gravity is excluded (zero at rest)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        group = QGroupBox("Driving calibration")
        form = QFormLayout(group)
        self.range_combo = QComboBox()
        self.range_combo.addItems(["All moving GPS segments", "Visible plot range"])
        self.range_combo.setToolTip(
            "Use forward driving with acceleration/braking and turns or changes of grade"
        )
        form.addRow("Calibration samples", self.range_combo)
        self.acceleration_unit = QComboBox()
        self.acceleration_unit.addItems(["g", "m/s²"])
        self.acceleration_unit.setCurrentText(
            "m/s²" if session.metadata.acceleration_unit == "m/s²" else "g"
        )
        form.addRow("ACC values are in", self.acceleration_unit)
        self.gyro_unit = QComboBox()
        self.gyro_unit.addItems(["deg/s", "rad/s"])
        self.gyro_unit.setCurrentText(
            "rad/s" if session.metadata.gyroscope_unit == "rad/s" else "deg/s"
        )
        form.addRow("GYRO values are in", self.gyro_unit)
        self.zero_bank = QCheckBox("Selected driving has negligible mean bank and sideslip")
        self.zero_bank.setChecked(True)
        self.zero_bank.setToolTip(
            "Single-antenna GPS cannot separate mounting roll from unknown mean vehicle"
            " bank. Uphill/downhill grade is included when altitude is available"
        )
        form.addRow(self.zero_bank)
        layout.addWidget(group)
        timing = QGroupBox("Timing and confidence")
        form = QFormLayout(timing)
        has_clock = any(k in session.channels for k in ("T_IMU_US", "T_US", "IMU_T_US"))
        label = QLabel(
            "Monotonic IMU clock found."
            if has_clock
            else (
                "No monotonic IMU clock. Missing/backward logger times need estimates; "
                "position confidence is reduced."
            )
        )
        label.setWordWrap(True)
        form.addRow(label)
        if "GPS_ALT_MSL_M" not in session.channels:
            height_note = QLabel(
                "No GPS height: calibration additionally assumes a road with negligible grade."
            )
            height_note.setWordWrap(True)
            form.addRow(height_note)
        self.allow_estimated = QCheckBox("Allow estimated IMU intervals for legacy logs")
        self.allow_estimated.setChecked(True)
        form.addRow(self.allow_estimated)
        self.constraints = QCheckBox("Soft vehicle constraint during gentle motion")
        self.constraints.setToolTip(
            "Assumes small lateral/vertical body velocity. Leave off for drift, "
            "sideslip or substantial suspension motion"
        )
        form.addRow(self.constraints)
        layout.addWidget(timing)
        parameters = QGroupBox("GPS uncertainty and calibration smoothing")
        form = QFormLayout(parameters)
        self.fields = {}
        for name, label, value, lo, hi in (
            ("gps_position_sigma_m", "Horizontal GPS sigma [m]", 5, 0.2, 100),
            ("gps_altitude_sigma_m", "GPS altitude sigma [m]", 10, 0.5, 200),
            ("gps_speed_sigma_m_s", "GPS speed sigma [m/s]", 0.5, 0.05, 10),
            ("reference_window_s", "Calibration smoothing half-window [s]", 2.5, 1, 15),
        ):
            field = QDoubleSpinBox()
            field.setRange(lo, hi)
            field.setDecimals(2)
            field.setValue(value)
            self.fields[name] = field
            form.addRow(label, field)
        layout.addWidget(parameters)
        smoothing = QGroupBox("Load smoothing")
        form = QFormLayout(smoothing)
        self.load_cutoff = QComboBox()
        for text, frequency in (
            ("Smooth · 2 Hz", 2.0),
            ("Standard · 5 Hz", 5.0),
            ("Dynamic · 10 Hz", 10.0),
        ):
            self.load_cutoff.addItem(text, frequency)
        self.load_cutoff.setCurrentIndex(1)
        self.load_cutoff.setToolTip(
            "Creates CALC_G_LOAD_LP_X/Y/Z from the calculated loads using zero-phase "
            "Butterworth smoothing. Applies to these three output channels only. "
            "Run boundaries are excluded to limit edge effects."
        )
        form.addRow("CALC_G_LOAD_LP", self.load_cutoff)
        layout.addWidget(smoothing)
        note = QLabel(
            "Position starts only after valid GPS and a usable motion direction. During GPS loss, "
            "uncertainty grows. Constant speed on a constant straight slope alone cannot calibrate "
            "all parameters. Model uncertainty is not a guarantee of real position accuracy."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Calculate CALC signals")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def configuration(self):
        rows = None
        if self.range_combo.currentIndex() == 1:
            axis = self.session.axis(self.mode)
            lo = int(np.searchsorted(axis, self.bounds[0], side="left"))
            hi = int(np.searchsorted(axis, self.bounds[1], side="right")) - 1
            if lo > hi or lo >= len(axis) or hi < 0:
                raise ValueError("The visible range does not contain logger samples")
            rows = max(0, lo), min(len(axis) - 1, hi)
        return NavigationConfig(
            acceleration_unit=self.acceleration_unit.currentText(),
            gyro_unit=self.gyro_unit.currentText(),
            calibration_rows=rows,
            allow_estimated_time=self.allow_estimated.isChecked(),
            vehicle_constraint=self.constraints.isChecked(),
            assume_zero_mean_bank=self.zero_bank.isChecked(),
            load_cutoff_hz=self.load_cutoff.currentData(),
            **{name: field.value() for name, field in self.fields.items()},
        )

    def _accept(self):
        try:
            self.configuration().validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Check navigation settings", str(exc))
            return
        self.accept()
