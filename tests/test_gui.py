import json
import os
import subprocess
import sys

import pytest


@pytest.mark.gui
@pytest.mark.parametrize("folder", [False, True], ids=["single-file", "folder"])
def test_gui_import_plots_and_bidirectional_map(tmp_path, folder):
    environment = os.environ.copy()
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    if sys.platform == "linux":
        # Isolated CI only. The distributed app does not disable Chromium's sandbox.
        environment.update(
            QT_QPA_PLATFORM="offscreen",
            QT_QUICK_BACKEND="software",
            QT_OPENGL="software",
            QTWEBENGINE_DISABLE_SANDBOX="1",
            QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-dev-shm-usage",
        )
    source = "tests/fixtures/synthetic.TXT"
    if folder:
        from pathlib import Path

        header, *rows = Path(source).read_text().splitlines()
        source = tmp_path / "logs"
        source.mkdir()
        for name, chunk in [("LOG10.TXT", rows[150:]), ("LOG2.TXT", rows[:150])]:
            (source / name).write_text("\n".join([header, *chunk]))
    result_path = tmp_path / "smoke.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "telemetry_viewer",
            "--smoke-test",
            str(source),
            "--smoke-result",
            str(result_path),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result_data = json.loads(result_path.read_text())
    assert result_data["passed"]
    assert result_data["source_files"] == (2 if folder else 1)
    if folder:
        assert result_data["source_names"] == ["LOG2.TXT", "LOG10.TXT"]
    assert "GUI_SMOKE_PASS" in result.stdout


@pytest.mark.gui
def test_large_session_rendering_and_branding(tmp_path):
    environment = os.environ.copy()
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    if sys.platform == "linux":
        environment.update(
            QT_QPA_PLATFORM="offscreen",
            QT_QUICK_BACKEND="software",
            QT_OPENGL="software",
            QTWEBENGINE_DISABLE_SANDBOX="1",
            QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-dev-shm-usage",
        )
    result = subprocess.run(
        [sys.executable, "tools/check_rendering.py"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RENDER_CHECK_PASS" in result.stdout


@pytest.mark.gui
@pytest.mark.parametrize("compact", [False, True], ids=["desktop", "small-window"])
def test_plot_configuration_multi_unit_axes_and_x_only_wheel(tmp_path, compact):
    environment = os.environ.copy()
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    if sys.platform == "linux":
        environment.update(
            QT_QPA_PLATFORM="offscreen",
            QT_QUICK_BACKEND="software",
            QT_OPENGL="software",
            QTWEBENGINE_DISABLE_SANDBOX="1",
            QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-dev-shm-usage",
        )
    result = subprocess.run(
        [sys.executable, "tools/check_plot_config.py", *(["--compact"] if compact else [])],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PLOT_CONFIG_PASS" in result.stdout
