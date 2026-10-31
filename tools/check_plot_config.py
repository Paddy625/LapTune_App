"""Real Qt interaction check for independent Y axes, wheel zoom and plot configuration."""

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pyqtgraph as pg
from benchmark_rendering import synthetic_session
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QWheelEvent

from telemetry_viewer.app import create_application, isolated_settings
from telemetry_viewer.gui.main_window import MainWindow
from telemetry_viewer.gui.plot_config import PlotConfigurationDialog, channel_unit

preferences = isolated_settings()
app = create_application([])
window = MainWindow()
window.layer_combo.setCurrentText("Offline")
window.resize(1560, 920)
window.show()
session = synthetic_session(2000)
session.channels["ACC_Y"].samples *= 5
window.pending_path = Path("synthetic-config-check").resolve()
window.accept_session(session)
timer = QTimer(window)
state = {"phase": "start"}
selected = ["ACC_X", "GYRO_Z", "GPS_SPEED_KMH"]
units = {name: channel_unit(session.channels[name]) for name in selected}


def y_snapshot():
    return {
        (panel, unit): tuple(view.viewRange()[1])
        for panel, views in enumerate(window.plots.unit_views)
        for unit, view in views.items()
    }


def fail(message):
    print("PLOT_CONFIG_FAIL " + message, flush=True)
    timer.stop()
    window.close()
    app.exit(1)


def check():
    try:
        phase = state["phase"]
        if phase == "start":
            if not window.map_widget.is_ready:
                return
            state["phase"] = "dialog"
            dialog = PlotConfigurationDialog(
                session, 0, ["GPS_SPEED_KMH"], "Plot 1", window.plots.y_ranges[0], window
            )
            for name, item in dialog.channel_items.items():
                item.setCheckState(
                    0, Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked
                )
            assert set(dialog.range_fields) == set(units.values())
            dialog.title_edit.setText("Dynamics")
            limits = {
                units["ACC_X"]: (-2, 2),
                units["GYRO_Z"]: (-20, 20),
                units["GPS_SPEED_KMH"]: (0, 100),
            }
            for unit, (lo, hi) in limits.items():
                dialog.range_fields[unit][0].setValue(lo)
                dialog.range_fields[unit][1].setValue(hi)
            # Exercise the actual MainWindow dialog/apply path, accepting the real modal UI.
            QTimer.singleShot(20, dialog._accept)
            with patch(
                "telemetry_viewer.gui.main_window.PlotConfigurationDialog", return_value=dialog
            ):
                window.configure_plot(0)
            # Every header/menu must target its own plot, including non-first plots.
            from PySide6.QtWidgets import QToolButton

            captured = []
            window.plots.configure_requested.disconnect(window.configure_plot)

            def capture(panel, tab):
                captured.append((panel, tab))

            window.plots.configure_requested.connect(capture)
            for panel in range(4):
                window.plots.options_menus[panel].actions()[0].trigger()
                window.plots.options_menus[panel].actions()[1].trigger()
                button = (
                    window.plots.headers[panel]
                    .widget()
                    .findChild(QToolButton, f"configure_plot_{panel + 1}")
                )
                button.click()
            assert captured == [(p, t) for p in range(4) for t in (0, 1, 0)]
            window.plots.configure_requested.disconnect(capture)
            window.plots.configure_requested.connect(window.configure_plot)
            assert window.plots.titles[0] == "Dynamics"
            assert (0, "ACC_X") in window.plots.curves and (1, "ACC_X") in window.plots.curves
            assert len(window.plots.unit_views[0]) == 3
            assert len({id(v) for v in window.plots.unit_views[0].values()}) == 3
            assert window.plots.axes[0].tickStrings([0, 60, 3661, 90061], 1, 60) == [
                "00:00:00",
                "00:01:00",
                "01:01:01",
                "25:01:01",
            ]
            assert window.brand_logo.pixmap().height() == window.brand_toolbar.height()
            assert window.brand_logo.height() == window.brand_toolbar.height()
            state["phase"] = "wheel"
        elif phase == "wheel":
            # The drawn unit labels must contain real colored text, not clipped bitmaps.
            image = window.grab().toImage()
            ratio = window.devicePixelRatioF()
            viewport = window.plots.graphics.viewport()
            for axes in window.plots.unit_axes[:3]:
                for axis in axes.values():
                    rect = window.plots.graphics.mapFromScene(
                        axis.label.sceneBoundingRect()
                    ).boundingRect()
                    origin = viewport.mapTo(window, rect.topLeft())
                    expected = axis.pen().color()
                    hits = 0
                    for x in range(
                        int(origin.x() * ratio), int((origin.x() + rect.width()) * ratio)
                    ):
                        for y in range(
                            int(origin.y() * ratio), int((origin.y() + rect.height()) * ratio)
                        ):
                            if 0 <= x < image.width() and 0 <= y < image.height():
                                pixel = image.pixelColor(x, y)
                                if (
                                    max(
                                        abs(pixel.red() - expected.red()),
                                        abs(pixel.green() - expected.green()),
                                        abs(pixel.blue() - expected.blue()),
                                    )
                                    < 65
                                ):
                                    hits += 1
                    assert hits > 10, "A Y-axis unit label was clipped"
            # Assigning one channel to several plots must not duplicate CSV columns.
            from PySide6.QtWidgets import QFileDialog

            with TemporaryDirectory() as export_folder:
                target = Path(export_folder) / "selected.csv"
                session.timestamp_raw = ["2026-01-01 12:00:00.000"] * len(session.nr)
                with patch.object(
                    QFileDialog, "getSaveFileName", return_value=(str(target), "CSV")
                ):
                    window.export_menu.actions()[0].trigger()
                with target.open() as handle:
                    header = next(csv.reader(handle))
                assert sum(name.startswith("ACC_X [") for name in header) == 1
            state["y_before"] = y_snapshot()
            state["x_before"] = tuple(window.plots.plots[0].vb.viewRange()[0])
            center = window.plots.plots[0].vb.sceneBoundingRect().center()
            viewport = window.plots.graphics.viewport()
            pos = window.plots.graphics.mapFromScene(center)
            wheel = QWheelEvent(
                QPointF(pos),
                QPointF(viewport.mapToGlobal(pos)),
                QPoint(),
                QPoint(0, 120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
            app.sendEvent(viewport, wheel)
            state["phase"] = "verify_wheel"
        elif phase == "verify_wheel":
            before = state["x_before"]
            after = window.plots.plots[0].vb.viewRange()[0]
            assert after[1] - after[0] < before[1] - before[0], "Wheel did not zoom X"
            assert y_snapshot() == state["y_before"], "Wheel changed a Y range"
            for view in window.plots.unit_views[0].values():
                np.testing.assert_allclose(view.viewRange()[0], after, atol=1e-6)
                assert not view.autoRangeEnabled()[1]
                assert not view.state["mouseEnabled"][1]
            for plot in window.plots.plots[1:]:
                np.testing.assert_allclose(plot.vb.viewRange()[0], after, atol=1e-6)

            # Forwarding a wheel from a Y axis must also change X only.
            class AxisWheel:
                def delta(self):
                    return 120

                def pos(self):
                    return QPointF(30, 30)

                def accept(self):
                    pass

                def ignore(self):
                    pass

            extra = list(window.plots.unit_views[0].values())[-1]
            extra.wheelEvent(AxisWheel(), axis=1)
            assert y_snapshot() == state["y_before"]

            # Dragging over an auxiliary unit layer must pan the shared X range.
            class Drag:
                def pos(self):
                    return pg.Point(120, 50)

                def lastPos(self):
                    return pg.Point(100, 50)

                def button(self):
                    return Qt.MouseButton.LeftButton

                def accept(self):
                    pass

            x_before_drag = tuple(window.plots.plots[0].vb.viewRange()[0])
            extra.mouseDragEvent(Drag())
            assert tuple(window.plots.plots[0].vb.viewRange()[0]) != x_before_drag
            assert y_snapshot() == state["y_before"]
            # A channel checkbox is not an auto-range command; existing Y limits stay fixed.
            item = window.channels.items["ACC_Y"][0]
            item.setCheckState(0, Qt.CheckState.Unchecked)
            assert y_snapshot() == state["y_before"]
            item.setCheckState(0, Qt.CheckState.Checked)
            assert y_snapshot() == state["y_before"]
            window.plots.fit_all()
            assert y_snapshot() == state["y_before"], "Fit X changed Y"
            window.axis_combo.setCurrentText("Distance")
            assert y_snapshot() == state["y_before"], "Axis-mode change modified Y"
            window.axis_combo.setCurrentText("Time")
            assert y_snapshot() == state["y_before"]
            # A non-first plot's options must modify that plot's Y range only.
            window.plots.set_y_ranges(1, {units["ACC_X"]: (-5, 5)})
            before_second_fit = y_snapshot()
            window.plots.options_menus[1].actions()[2].trigger()
            after_second_fit = y_snapshot()
            assert after_second_fit[(1, units["ACC_X"])] != before_second_fit[(1, units["ACC_X"])]
            for key, value in before_second_fit.items():
                if key[0] != 1:
                    assert after_second_fit[key] == value
            # Removing and restoring a unit must not accumulate stale axes or scenes.
            for _ in range(3):
                window.channels.apply_plot_selection(0, ["ACC_X", "GPS_SPEED_KMH"])
                assert len(window.plots.unit_views[0]) == 2
                window.channels.apply_plot_selection(0, selected)
                assert len(window.plots.unit_views[0]) == 3
            state["phase"] = "persist"
        elif phase == "persist":
            # Only the explicit options action may refit a Y scale.
            before = y_snapshot()
            window.plots.options_menus[0].actions()[2].trigger()
            after = y_snapshot()
            assert after[(0, units["ACC_X"])] != before[(0, units["ACC_X"])]
            for key, value in before.items():
                if key[0] != 0:
                    assert after[key] == value
            saved = json.loads(window.settings.value("plot_layout_v2"))
            assert saved["plots"][0]["title"] == "Dynamics"
            assert set(saved["plots"][0]["channels"]) == set(selected)
            clone = MainWindow()
            clone.pending_path = Path("synthetic-config-check").resolve()
            clone.accept_session(session)
            assert clone.plots.titles[0] == "Dynamics"
            assert set(clone.plots.unit_views[0]) == set(units.values())
            for unit, view in clone.plots.unit_views[0].items():
                np.testing.assert_allclose(view.viewRange()[1], after[(0, unit)])
            clone.channels.apply_plot_selection(3, list(session.channels))
            assert len(clone.plots.unit_views[3]) == 4
            assert all((3, name) in clone.plots.curves for name in session.channels)
            clone.close()
            print(
                "PLOT_CONFIG_PASS "
                + json.dumps(
                    {
                        "units": list(units.values()),
                        "wheel": "X only",
                        "clock": "hh:mm:ss",
                        "duplicate_channels": True,
                    }
                ),
                flush=True,
            )
            timer.stop()
            window.close()
            app.quit()
    except Exception as exc:
        import traceback

        fail(traceback.format_exc() + repr(exc))


timer.timeout.connect(check)
timer.start(250)
QTimer.singleShot(30000, lambda: fail("Timed out"))
raise SystemExit(app.exec())
