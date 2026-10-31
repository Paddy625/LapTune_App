"""Generate a wholly synthetic loop with independently changing GPS fields."""

import argparse
import math
from datetime import datetime, timedelta
from pathlib import Path

HEADER = "NR;DATA;CZAS;ACC_X;ACC_Y;ACC_Z;GYRO_X;GYRO_Y;GYRO_Z;GPS_LAT;GPS_LON;GPS_SPEED_KMH"


def generate(path: Path, rows: int = 18640):
    origin = datetime(2026, 1, 1, 12)
    path.parent.mkdir(parents=True, exist_ok=True)
    next_fix, fix_t = 0.0, 0.0
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(HEADER + "\r\n")
        for i in range(rows):
            t = i / 100
            if t >= next_fix:
                fix_t = t
                # Refresh schedule varies: no fixed 1 Hz assumption.
                next_fix += (0.2, 0.5, 1.0, 2.0)[int(t / 15) % 4]
            angle = fix_t / 35
            lat, lon = 0.003 * math.sin(angle), 0.004 * math.cos(angle)
            speed_t = math.floor(t * 5) / 5  # Speed refreshes independently at 5 Hz.
            speed = 42 + 14 * math.sin(speed_t / 12)
            stamp = origin + timedelta(seconds=t)
            handle.write(
                f"{i + 70000};{stamp:%Y-%m-%d};{stamp:%H:%M:%S.%f};"
                f"{0.2 * math.sin(t / 2):.6f};{0.3 * math.cos(t / 3):.6f};"
                f"{1 + 0.05 * math.sin(t * 4):.6f};{2 * math.sin(t):.3f};"
                f"{3 * math.cos(t):.3f};{12 * math.sin(t / 4):.3f};"
                f"{lat:.8f};{lon:.8f};{speed:.3f}\r\n"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rows", type=int, default=18640)
    args = parser.parse_args()
    if args.rows < 2:
        parser.error("At least two rows are required")
    generate(args.output, args.rows)
