import json
import os
import subprocess
import sys

import pytest


@pytest.mark.gui
def test_gui_import_plots_and_bidirectional_map(tmp_path):
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
    result_path = tmp_path / "smoke.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "telemetry_viewer",
            "--smoke-test",
            "tests/fixtures/synthetic.TXT",
            "--smoke-result",
            str(result_path),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result_path.read_text())["passed"]
    assert "GUI_SMOKE_PASS" in result.stdout
