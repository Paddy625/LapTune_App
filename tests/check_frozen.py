"""Run on Windows after PyInstaller. Fails if the packaged WebEngine cannot run."""

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from generate_navigation_demo import write_demo

bundle = Path(sys.argv[1]).resolve()
for pattern in (
    "QtWebEngineProcess.exe",
    "qtwebengine_resources.pak",
    "icudtl.dat",
    "en-US.pak",
    "laptune-logo.png",
):
    if not list(bundle.rglob(pattern)):
        raise RuntimeError(f"Required frozen WebEngine resource missing: {pattern}")
with TemporaryDirectory() as temp:
    result = Path(temp) / "smoke.json"
    subprocess.run(
        [
            str(bundle / "TelemetryViewer.exe"),
            "--smoke-test",
            str(Path("tests/fixtures/synthetic.TXT").resolve()),
            "--smoke-result",
            str(result),
        ],
        check=True,
        timeout=75,
    )
    assert json.loads(result.read_text())["passed"]
    print("Frozen Windows GUI, plots, offline Leaflet and WebChannel smoke passed")
    navigation_log = write_demo(Path(temp) / "synthetic-navigation.TXT")
    navigation_result = Path(temp) / "navigation-smoke.json"
    subprocess.run(
        [
            str(bundle / "TelemetryViewer.exe"),
            "--smoke-navigation",
            str(navigation_log),
            "--smoke-result",
            str(navigation_result),
        ],
        check=True,
        timeout=75,
    )
    data = json.loads(navigation_result.read_text())
    assert data["passed"] and data["estimated_channels"] >= 20
    print("Frozen Windows EST driving calibration, ESKF, GPS-loss and map smoke passed")
