"""Units, groups and descriptions for numeric firmware diagnostics and GPS status."""

LOGGER_FIELDS = {
    "T_ACC_US": ("µs", "Timing", "ACC DRDY timestamp in the monotonic MCU clock"),
    "T_GYRO_US": ("µs", "Timing", "GYRO DRDY timestamp in the monotonic MCU clock"),
    "GPS_T_RX_US": ("µs", "Timing", "GPS epoch reception time in the MCU clock"),
    "GPS_T_MEAS_US": ("µs", "Timing", "GPS measurement time mapped into the MCU clock by PPS"),
    "TIME_SYNC_STATE": (
        "code",
        "Timing",
        "0 unsynchronized; 1 PPS locked; 2 holdover",
    ),
    "GPS_STATE": (
        "code",
        "GPS status",
        "GPS epoch state: 0=I invalid, 1=N new valid epoch, 2=R repeated epoch",
    ),
    "GPS_FIX_ID": ("count", "GPS status", "GPS epoch identifier; unchanged for repetitions"),
    "GPS_ITOW_MS": ("ms", "GPS status", "GNSS measurement epoch, milliseconds of GPS week"),
    "GPS_NEW": ("flag", "GPS status", "1 new GPS epoch; 0 no new epoch"),
    "GPS_VALID": ("flag", "GPS status", "1 valid GPS position; 0 invalid position"),
    "IMU_VALID": ("flag", "Logger diagnostics", "1 valid IMU vector pair; 0 invalid IMU data"),
    "IMU_FLAGS": (
        "bitmask",
        "Logger diagnostics",
        "IMU fault bits: SPI=1, FRESH=2, PAIR=4, CLIP=8, TIMEOUT=16; 0 no flags",
    ),
    "IMU_DROPPED": ("count", "Logger diagnostics", "Cumulative discarded/lost IMU samples"),
    "IMU_SPI_ERRORS": ("count", "Logger diagnostics", "Cumulative IMU SPI errors"),
    "IMU_PAIR_ERRORS": ("count", "Logger diagnostics", "Cumulative ACC/GYRO pairing errors"),
    "IMU_MISSED_DRDY": ("count", "Logger diagnostics", "Cumulative missed IMU DRDY events"),
    "GPS_UART_ERRORS": ("count", "Logger diagnostics", "Cumulative GNSS UART errors"),
    "GPS_CHECKSUM_ERRORS": ("count", "Logger diagnostics", "Cumulative GNSS checksum errors"),
    "GPS_INCOMPLETE_EPOCHS": ("count", "Logger diagnostics", "Cumulative incomplete GPS epochs"),
    "GPS_LATE_EPOCHS": ("count", "Logger diagnostics", "Cumulative late GPS epoch fragments"),
    "GPS_RX_OVERFLOWS": ("count", "Logger diagnostics", "Cumulative GNSS receive buffer overflows"),
    "GPS_INVALID_FIXES": (
        "count",
        "Logger diagnostics",
        "Cumulative GPS epochs without valid fixes",
    ),
    "SD_QUEUE_DROPPED": (
        "count",
        "Logger diagnostics",
        "Cumulative records lost from the SD queue",
    ),
    "SD_MAX_PAUSE_US": ("µs", "Logger diagnostics", "Maximum observed SD write pause"),
}

VALUE_LABELS = {
    "GPS_STATE": {0: "I", 1: "N", 2: "R"},
    "TIME_SYNC_STATE": {0: "Unsynchronized", 1: "PPS locked", 2: "Holdover"},
    "IMU_VALID": {0: "Invalid", 1: "Valid"},
}


def extra_field(name):
    """Keep future logger/GNSS diagnostics out of the generic CAN fallback."""
    if name in LOGGER_FIELDS:
        return LOGGER_FIELDS[name]
    if name.startswith(("GPS_T_", "TIME_", "T_")):
        group = "Timing"
    elif name.startswith(("IMU_", "SD_", "PPS_")):
        group = "Logger diagnostics"
    elif name.startswith("GPS_"):
        group = "GPS status"
    else:
        return "", "CAN / Other", ""
    unit = "µs" if name.endswith("_US") else ("ms" if name.endswith("_MS") else "")
    return unit, group, "Additional logger field; original recorded values preserved"
