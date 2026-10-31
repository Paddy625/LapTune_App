"""Run on Windows after PyInstaller. Fails if the packaged WebEngine cannot run."""

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

bundle = Path(sys.argv[1]).resolve()
for pattern in ("QtWebEngineProcess.exe", "qtwebengine_resources.pak", "icudtl.dat", "en-US.pak"):
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
