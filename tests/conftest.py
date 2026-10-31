from datetime import datetime, timedelta

import pytest

HEADER = "NR;DATA;CZAS;ACC_X;ACC_Y;ACC_Z;GYRO_X;GYRO_Y;GYRO_Z;GPS_LAT;GPS_LON;GPS_SPEED_KMH"


def synthetic_text(count=301, step=0.01):
    origin = datetime(2026, 1, 1, 23, 59, 59)
    rows = [HEADER]
    for i in range(count):
        t = origin + timedelta(seconds=i * step)
        fix = int(i * step)
        # Equator: one longitude degree ~= 111195.08 m, 10 m/s.
        lon = fix * 10 / 111195.0802335329
        rows.append(f"{70000 + i};{t:%Y-%m-%d};{t:%H:%M:%S.%f};0.1;0.2;1;2;3;4;0;{lon:.10f};36")
    return "\r\n".join(rows) + "\r\n"


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "synthetic.TXT"
    path.write_text(synthetic_text(), encoding="utf-8", newline="")
    return path
