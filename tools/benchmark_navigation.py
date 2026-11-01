"""Synthetic ground-truth checks; never records or displays a private route."""

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np
from generate_navigation_demo import trajectory, write_demo

from telemetry_viewer.importers.legacy_txt import LegacyTxtImporter
from telemetry_viewer.processing.navigation import NavigationConfig, estimate_navigation
from telemetry_viewer.processing.rotations import rotation_log


def benchmark():
    scenarios = []
    with TemporaryDirectory(prefix="laptune-navigation-benchmark-") as temporary:
        for seed in (23, 24, 25):
            truth = trajectory(seed=seed)
            session = LegacyTxtImporter().load(write_demo(Path(temporary) / "synthetic.TXT", truth))
            before = {
                k: hashlib.sha256(c.samples.tobytes()).digest() for k, c in session.channels.items()
            }
            started = perf_counter()
            result = estimate_navigation(
                session, NavigationConfig(gps_position_sigma_m=1, gps_altitude_sigma_m=2)
            )
            elapsed = perf_counter() - started
            channels = result.channels
            position = np.column_stack(
                (
                    np.radians(channels["EST_LON"].samples) * 6378137,
                    np.radians(channels["EST_LAT"].samples) * 6335439.3272928,
                )
            )
            error = np.linalg.norm(position - truth["position"][:, :2], axis=1)
            gps_rows = np.array([text == "N" for text in session.logger_fields["GPS_STATE"]])
            aided = gps_rows & channels["EST_LAT"].valid
            dropout = (truth["time"] >= 28) & (truth["time"] < 35)
            raw_error = np.linalg.norm(
                truth["measured_position"][:, :2] - truth["position"][:, :2], axis=1
            )
            calibration = result.report["calibration"]
            scenarios.append(
                {
                    "seed": seed,
                    "rows": len(session.nr),
                    "compute_seconds": round(elapsed, 4),
                    "mounting_error_deg": round(
                        float(
                            np.degrees(
                                np.linalg.norm(
                                    rotation_log(
                                        np.array(calibration["rotation_sensor_to_vehicle"])
                                        @ truth["mounting"].T
                                    )
                                )
                            )
                        ),
                        4,
                    ),
                    "raw_gps_horizontal_rmse_m": round(
                        float(np.sqrt(np.mean(raw_error[aided] ** 2))), 4
                    ),
                    "fused_at_gps_epochs_horizontal_rmse_m": round(
                        float(np.sqrt(np.mean(error[aided] ** 2))), 4
                    ),
                    "whole_estimated_track_horizontal_rmse_m": round(
                        float(np.sqrt(np.nanmean(error**2))), 4
                    ),
                    "seven_second_gps_loss_max_error_m": round(float(np.nanmax(error[dropout])), 4),
                    "gps_loss_output_complete": bool(channels["EST_LAT"].valid[dropout].all()),
                    "raw_arrays_unchanged": before
                    == {
                        k: hashlib.sha256(c.samples.tobytes()).digest()
                        for k, c in session.channels.items()
                    },
                }
            )
    return {
        "input": "Independent synthetic 80 s drive; 50 Hz IMU; 5 Hz GPS; 7 s GPS loss; "
        "nontrivial mounting; constant sensor biases; 0.15 m GPS axis noise",
        "limitations": "Offline driving calibration uses the selected whole drive; "
        "no real-world accuracy or timing guarantee. Single-antenna mean-bank assumption. "
        "No RTS smoothing.",
        "scenarios": scenarios,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = benchmark()
    output = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
