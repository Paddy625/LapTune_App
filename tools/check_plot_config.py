"""Real Qt interaction check for independent Y axes, wheel zoom and plot configuration."""

import csv
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pyqtgraph as pg
from benchmark_rendering import synthetic_session
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QWheelEvent

from telemetry_viewer.app import create_application, isolated_settings
from telemetry_viewer.gui.main_window import MainWindow
from telemetry_viewer.gui.plot_config import PlotConfigurationDialog, channel_unit
from telemetry_viewer.gui.signal_tools import SignalToolsDialog, StatisticsDialog

preferences = isolated_settings()
app = create_application([])
window = MainWindow()
assert Path(window.settings.fileName()).is_relative_to(preferences.name), (
    "Settings were not isolated"
)
window.layer_combo.setCurrentText("Offline")
window.resize(1560, 920)
if "--compact" in sys.argv:
    window.resize(1028, 749)
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
            assert window.map_widget._last_range is not None, "Initial map range was not sent"
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
            assert window.plots.axes[0].tickSpacing(0, 20, 200)[0][0] >= 10
            assert window.brand_logo.pixmap().height() == window.brand_toolbar.height()
            assert window.brand_logo.height() == window.brand_toolbar.height()
            state["phase"] = "wheel"
        elif phase == "wheel":
            # The drawn unit labels must contain real colored text, not clipped bitmaps.
            image = window.grab().toImage()
            viewport = window.plots.graphics.viewport()
            assert window.plots.title_labels[0].width() > 50
            assert all(
                item.data(0, Qt.ItemDataRole.CheckStateRole) is None
                and not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
                for item, _panels, _color in window.channels.items.values()
            ), "Channel names must not have checkbox indicators"
            assert all(
                action.text().replace("&", "") != "Analysis"
                for action in window.menuBar().actions()
            )
            assert window.analysis_footer.y() > window.channels.geometry().bottom()
            assert window.channel_content.height() - window.analysis_footer.geometry().bottom() <= 1
            assert window.calculate_button.defaultAction() is window.signal_tools_action
            assert window.statistics_button.defaultAction() is window.statistics_action
            assert window.brand_logo.isVisibleTo(window), "Logo overflowed the toolbar"
            assert window.rect().contains(
                window.brand_logo.mapTo(window, window.brand_logo.rect().bottomRight())
            ), "Logo was outside the window"
            assert (
                abs(
                    window.width()
                    - 1
                    - window.brand_logo.mapTo(window, window.brand_logo.rect().bottomRight()).x()
                )
                <= 2
            ), "Logo was not aligned to the right edge"
            assert window.plots.plots[0].vb.width() >= 190, "Dock panels squeezed the plot"
            right = window.plots.graphics.mapFromScene(
                window.plots.plots[0].vb.sceneBoundingRect().topRight()
            ).x()
            assert viewport.width() - right >= 30, "The last clock label was clipped"
            ratio = window.devicePixelRatioF()
            for panel, axes in enumerate(window.plots.unit_axes[:3]):
                for unit, axis in axes.items():
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
                    if hits <= 10:
                        screenshot = Path("test-artifacts/plot-config-failure.png")
                        screenshot.parent.mkdir(parents=True, exist_ok=True)
                        image.save(str(screenshot))
                        raise AssertionError(
                            f"A Y-axis unit label was clipped: panel={panel}, unit={unit}, "
                            f"hits={hits}, expected={expected.name()}, ratio={ratio}, "
                            f"image={image.size()}, origin={origin}, label={rect}, "
                            f"visible={axis.label.isVisible()}, screenshot={screenshot}"
                        )
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
            # Changing a plot's channel selection must leave its existing Y limits fixed.
            acceleration = [name for name, panel, _ in window.channels.selection() if panel == 1]
            window.channels.apply_plot_selection(
                1, [name for name in acceleration if name != "ACC_Y"]
            )
            assert y_snapshot() == state["y_before"]
            window.channels.apply_plot_selection(1, acceleration)
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
            # Configuring channels must never override an explicit visible-panel count.
            for count in (1, 2, 4, 2):
                window.count_combo.setCurrentIndex(count - 1)
                window.channels.apply_plot_selection(0, selected)
                window.refresh_plots()
                assert window.count_combo.currentIndex() + 1 == count
                assert sum(plot.isVisible() for plot in window.plots.plots) == count
                assert all(
                    not view.isVisible()
                    for extras in window.plots._extras[count:]
                    for _unit, view, _axis, _column in extras
                )
            assert window.channels.columnCount() == 2
            assert window.channels.headerItem().text(1) == "Value"
            dialog = PlotConfigurationDialog(
                session, 1, ["ACC_X", "ACC_Y"], "Acceleration", window.plots.y_ranges[1], window
            )
            QTimer.singleShot(20, dialog._accept)
            with patch(
                "telemetry_viewer.gui.main_window.PlotConfigurationDialog", return_value=dialog
            ):
                window.configure_plot(1)
            assert window.count_combo.currentIndex() == 1
            assert window.plots.plots[1].isVisible() and not window.plots.plots[2].isVisible()
            # A single source color applies everywhere without recreating curve data.
            from PySide6.QtWidgets import QColorDialog, QMenu

            window.set_cursor(777)
            expected = [f"{session.channels[name].samples[777]:.3f}" for name in selected]
            # selected is intentionally ordered like the legend: ACC, gyro, speed.
            assert window.plots.cursor_readouts[0].textItem.toPlainText().splitlines() == expected
            assert not window.plots.cursor_readouts[2].isVisible()
            session.channels["ACC_X"].valid[777] = False
            window.set_cursor(777)
            assert window.plots.cursor_readouts[0].textItem.toPlainText().splitlines()[0] == "—"
            session.channels["ACC_X"].valid[777] = True
            window.set_cursor(777)
            curves_before = {key: id(curve) for key, (curve, _) in window.plots.curves.items()}
            ranges_before = y_snapshot()
            x_before = tuple(window.plots.plots[0].vb.viewRange()[0])

            class ColorMenu(QMenu):
                def exec(self, _pos):
                    return next(
                        action for action in self.actions() if action.text() == "Change color…"
                    )

            with (
                patch("telemetry_viewer.gui.channel_panel.QMenu", ColorMenu),
                patch.object(QColorDialog, "getColor", return_value=QColor("#43e8c1")),
            ):
                item = window.channels.items["ACC_X"][0]
                window.channels._menu(window.channels.visualItemRect(item).center())
            assert window.channels.items["ACC_X"][2] == "#43e8c1"
            assert window.channels.items["ACC_X"][0].foreground(0).color().name() == "#43e8c1"
            assert {
                key: id(curve) for key, (curve, _) in window.plots.curves.items()
            } == curves_before
            for panel in (0, 1):
                assert (
                    window.plots.curves[(panel, "ACC_X")][0].opts["pen"].color().name() == "#43e8c1"
                )
                assert "#43e8c1" in window.plots.cursor_readouts[panel].textItem.toHtml()
                assert (
                    window.plots.unit_axes[panel][units["ACC_X"]].pen().color().name() == "#43e8c1"
                )
            assert y_snapshot() == ranges_before
            assert tuple(window.plots.plots[0].vb.viewRange()[0]) == x_before
            saved_colors = window.settings.value("plot_layout_v2")
            assert json.loads(saved_colors)["colors"]["ACC_X"] == "#43e8c1"
            with patch.object(QColorDialog, "getColor", return_value=QColor()):
                window.channels.choose_color("ACC_X")
            assert window.settings.value("plot_layout_v2") == saved_colors
            # Keep the numeric-only stack inside the plot at either X boundary.
            window.plots.plots[0].setXRange(0, 20, padding=0)
            for row in (0, len(session.nr) - 1):
                window.set_cursor(row)
                for panel in (0, 1):
                    label = window.plots.cursor_readouts[panel]
                    bounds = window.plots.plots[panel].vb.sceneBoundingRect()
                    assert label.isVisible()
                    assert label.sceneBoundingRect().left() >= bounds.left()
                    assert label.sceneBoundingRect().right() <= bounds.right()
            window.plots.plots[0].setXRange(25, 30, padding=0)
            assert not any(label.isVisible() for label in window.plots.cursor_readouts)
            window.plots.plots[0].setXRange(*x_before, padding=0)
            window.set_cursor(777)
            state["raw_before"] = session.channels["ACC_X"].samples.copy()
            tools = SignalToolsDialog(session, window, source="ACC_X")
            # Hide the whole source row, including its label, for unary operations.
            for operation in (
                "magnitude",
                "difference",
                "scale",
                "convert",
                "derivative",
                "integral",
                "highpass",
                "lowpass",
            ):
                tools.operation.setCurrentIndex(tools.operation.findData(operation))
                for index, visible in (
                    (1, operation in ("magnitude", "difference")),
                    (2, operation == "magnitude"),
                ):
                    assert tools.form.isRowVisible(tools.sources[index]) == visible
                    assert tools.sources[index].isEnabled() == visible
                    assert tools.form.labelForField(tools.sources[index]).isHidden() != visible
                if operation not in ("magnitude", "difference"):
                    assert tools.recipe().sources == ("ACC_X",)
            tools.cutoff.setValue(3)
            tools.name.setText("CALC_ACC_FILTERED")
            QTimer.singleShot(20, tools._accept)
            window.channels.setCurrentItem(window.channels.items["ACC_X"][0])
            with patch(
                "telemetry_viewer.gui.main_window.SignalToolsDialog", return_value=tools
            ) as factory:
                window.calculate_button.click()
                assert factory.call_args.kwargs["source"] == "ACC_X"
            state["phase"] = "calculated"
        elif phase == "calculated":
            if window.calculation_thread is not None:
                return
            derived = session.channels["CALC_ACC_FILTERED"]
            assert derived.group == "Calculated" and derived.calculation["cutoff_hz"] == 3
            np.testing.assert_array_equal(session.channels["ACC_X"].samples, state["raw_before"])
            item = window.channels.items[derived.name][0]
            assert item.parent().text(0) == "Calculated"
            assert item.data(0, Qt.ItemDataRole.CheckStateRole) is None
            assert not any(
                name == derived.name for name, _panel, _color in window.channels.selection()
            )
            window.channels.apply_plot_selection(0, [derived.name, "GYRO_Z", "GPS_SPEED_KMH"])
            assert (0, derived.name) in window.plots.curves
            assert window.count_combo.currentIndex() == 1
            stats = StatisticsDialog(session, "time", (3, 5), window)
            row = next(
                stats.table.topLevelItem(i)
                for i in range(stats.table.topLevelItemCount())
                if stats.table.topLevelItem(i).text(0) == derived.name
            )
            assert int(row.text(2)) == 201
            stats.scope.setCurrentIndex(1)
            assert int(
                stats.table.topLevelItem(stats.table.topLevelItemCount() - 1).text(2)
            ) == len(session.nr)
            QTimer.singleShot(20, stats.accept)
            with patch("telemetry_viewer.gui.main_window.StatisticsDialog", return_value=stats):
                window.statistics_button.click()
            window.plots.plots[0].setXRange(3, 5, padding=0)

            def center_dialog(*args, **kwargs):
                dialog = SignalToolsDialog(*args, **kwargs)
                assert dialog.mode == "time"
                np.testing.assert_allclose(dialog.bounds, (3, 5))
                dialog.operation.setCurrentIndex(dialog.operation.findData("scale"))
                dialog.dc_mode.setCurrentIndex(dialog.dc_mode.findData("visible"))
                dialog.name.setText("CALC_DC_ZERO")
                QTimer.singleShot(20, dialog._accept)
                return dialog

            with patch(
                "telemetry_viewer.gui.main_window.SignalToolsDialog", side_effect=center_dialog
            ):
                window.calculate_button.click()
            state["phase"] = "centered"
        elif phase == "centered":
            if window.calculation_thread is not None:
                return
            centered = session.channels["CALC_DC_ZERO"]
            baseline = state["raw_before"][300:501].mean()
            np.testing.assert_allclose(centered.samples, state["raw_before"] - baseline, atol=1e-14)
            assert abs(centered.samples[300:501].mean()) < 1e-14
            assert centered.calculation["mean_time_range_s"] == (3, 5)
            assert centered.calculation["removed_mean"] == baseline
            assert window.count_combo.currentIndex() == 1
            window.channels.apply_plot_selection(1, [centered.name])
            assert (1, centered.name) in window.plots.curves
            # A distance viewport calibrates the same source-time interval.
            distance_dialog = SignalToolsDialog(
                session, window, source="ACC_X", mode="distance", bounds=(36, 60)
            )
            distance_dialog.operation.setCurrentIndex(distance_dialog.operation.findData("scale"))
            distance_dialog.dc_mode.setCurrentIndex(distance_dialog.dc_mode.findData("visible"))
            assert distance_dialog.recipe().mean_time_range_s == (3, 5)
            distance_dialog.close()
            # Verify independent range rendering in the real offline Leaflet page.
            window.map_widget.page.runJavaScript(
                "routeRenderer.rangeTestRedraws=0;"
                "const previous=routeRenderer._redraw;"
                "routeRenderer._redraw=function(){this.rangeTestRedraws++;"
                "return previous.apply(this,arguments);};"
            )
            window.plots.plots[0].setXRange(3, 11, padding=0)
            state["phase"] = "map_time"
        elif phase in ("map_time", "map_distance"):
            state["phase"] = "map_query_pending"
            mode = phase
            window.map_widget.page.runJavaScript(
                "JSON.stringify({bounds:visibleRange, count:rangeMarks.getLayers().length,"
                "separate:rangeMarks.getLayers().every(p=>p._renderer!==routeRenderer),"
                "redraws:routeRenderer.rangeTestRedraws,"
                "bars:rangeMarks.getLayers().filter(p=>p.getLatLngs).map(p=>{"
                "const ends=p.getLatLngs().map(q=>map.latLngToLayerPoint(q));"
                "return ends[0].distanceTo(ends[1]);})})",
                lambda value, mode=mode: verify_range(value, mode),
            )
        elif phase == "map_empty":
            state["phase"] = "map_empty_pending"
            window.map_widget.page.runJavaScript(
                "JSON.stringify({bounds:visibleRange,count:rangeMarks.getLayers().length})",
                verify_empty_range,
            )
        elif phase == "marker_geometry":
            state["phase"] = "marker_geometry_pending"
            window.map_widget.page.runJavaScript(
                Path(__file__).with_name("check_marker_geometry.js").read_text(),
                verify_marker_geometry,
            )
        elif phase == "finish":
            clone = MainWindow()
            clone.pending_path = Path("synthetic-config-check").resolve()
            clone.accept_session(session)
            assert clone.count_combo.currentIndex() == 1
            assert sum(plot.isVisible() for plot in clone.plots.plots) == 2
            assert clone.channels.items["ACC_X"][2] == "#43e8c1"
            clone.channels.apply_plot_selection(0, ["ACC_X"])
            assert clone.plots.curves[(0, "ACC_X")][0].opts["pen"].color().name() == "#43e8c1"
            clone.close()
            print(
                "PLOT_CONFIG_PASS "
                + json.dumps(
                    {
                        "units": list(units.values()),
                        "wheel": "X only",
                        "clock": "hh:mm:ss",
                        "duplicate_channels": True,
                        "calculated": True,
                        "panel_count_preserved": True,
                        "map_range": ["time", "distance"],
                        "marker_geometry": "20 subpixel/reversal/endpoint/stationary zoom cases",
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


def verify_range(value, mode):
    try:
        value = json.loads(value)
        assert value["bounds"]["start"]["row"] == 300, value
        assert value["bounds"]["end"]["row"] == 1100, value
        assert value["count"] == 4 and value["separate"], value
        assert value["bars"] and all(abs(length - 28) < 2 for length in value["bars"]), value
        if mode == "map_time":
            assert value["redraws"] == 0, value
            window.axis_combo.setCurrentText("Distance")
            window.plots.plots[0].setXRange(36, 132, padding=0)
            state["phase"] = "map_distance"
        else:
            assert window.count_combo.currentIndex() == 1
            window.plots.plots[0].setXRange(100000, 100100, padding=0)
            state["phase"] = "map_empty"
    except Exception as exc:
        fail(f"Map range check: {exc}: {value}")


def verify_empty_range(value):
    try:
        value = json.loads(value)
        assert value["bounds"] is None and value["count"] == 0, value
        state["phase"] = "marker_geometry"
    except Exception as exc:
        fail(f"Empty map range check: {exc}: {value}")


def verify_marker_geometry(value):
    try:
        value = json.loads(value)
        assert "error" not in value, value
        assert len(value["results"]) == 20, value
        subpixel = [case for case in value["results"] if case["name"] == "subpixel"]
        assert all(case["roundedSamePixel"] for case in subpixel), value
        for case in value["results"]:
            assert case["count"] == 2 and case["separate"], case
            for bar in case["geometry"]:
                assert abs(bar["length"] - 28) < 1e-5, case
                if case["name"] == "stationary":
                    assert bar["unknown"], case
                else:
                    assert not bar["unknown"] and bar["perpendicular"] < 1e-6, case
        state["phase"] = "finish"
    except Exception as exc:
        fail(f"Marker geometry check: {exc}: {value}")


timer.timeout.connect(check)
timer.start(250)
QTimer.singleShot(30000, lambda: fail("Timed out"))
raise SystemExit(app.exec())
