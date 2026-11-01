"""Explicit local exports. Naive logger times never silently become UTC."""

import csv
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import numpy as np

from telemetry_viewer.domain.models import Session


class Exporter(Protocol):
    def export(self, session: Session, path: Path, channels: list[str]) -> None: ...


def export_csv(session: Session, path: Path, names: list[str]) -> None:
    path = Path(path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["row_index", "NR", "timestamp_raw", "elapsed_time_s [s]", "distance_m [m]"]
            + (["source_file", "source_line"] if session.source_files else [])
            + list(session.logger_fields)
            + [f"{n} [{session.channels[n].unit}]" for n in names]
        )
        aligned = []
        for name in names:
            channel = session.channels[name]
            indices = np.searchsorted(channel.time_s, session.elapsed_time_s)
            indices = np.clip(indices, 0, len(channel.time_s) - 1)
            valid = (channel.time_s[indices] == session.elapsed_time_s) & channel.valid[indices]
            aligned.append(np.where(valid, channel.samples[indices], np.nan))
        source_index = 0
        for i in range(len(session.nr)):
            provenance = []
            if session.source_files:
                while i >= session.source_files[source_index].end_row:
                    source_index += 1
                provenance = [
                    session.source_files[source_index].path.name,
                    int(session.source_lines[i]) if session.source_lines is not None else "",
                ]
            writer.writerow(
                [
                    i,
                    int(session.nr[i]),
                    session.timestamp_raw[i],
                    format(session.elapsed_time_s[i], ".9f"),
                    format(session.distance_m[i], ".9f"),
                ]
                + provenance
                + [values[i] for values in session.logger_fields.values()]
                + [float(a[i]) if np.isfinite(a[i]) else "" for a in aligned]
            )
    path.with_suffix(path.suffix + ".metadata.json").write_text(
        json.dumps(
            {
                "session": asdict(session.metadata),
                "distance_method": session.distance_method,
                "source_files": [
                    {
                        "name": source.path.name,
                        "start_row": source.start_row,
                        "end_row": source.end_row,
                    }
                    for source in session.source_files
                ],
                "channels": {
                    n: {
                        "unit": session.channels[n].unit,
                        "source": session.channels[n].source,
                        "interpolation": session.channels[n].interpolation,
                        "calculation": session.channels[n].calculation,
                        "description": session.channels[n].description,
                    }
                    for n in names
                },
                "alignment": (
                    "Exact shared row times; calculated and EST channels are explicitly identified"
                ),
                "navigation": session.navigation.report if session.navigation is not None else None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def export_gpx(session: Session, path: Path) -> None:
    if not len(session.gps_fix_samples):
        raise ValueError("This session has no accepted GPS fixes")
    ns, tv = "http://www.topografix.com/GPX/1/1", "https://telemetry-viewer.local/schema/1"
    ET.register_namespace("", ns)
    ET.register_namespace("tv", tv)
    root = ET.Element(f"{{{ns}}}gpx", version="1.1", creator="Telemetry Viewer")
    track = ET.SubElement(root, f"{{{ns}}}trk")
    ET.SubElement(track, f"{{{ns}}}name").text = session.filename
    segment = None
    tz = ZoneInfo(session.metadata.timezone) if session.metadata.timezone else None
    for i, starts in zip(session.gps_fix_samples, session.gps_segment_start, strict=True):
        if segment is None or starts:
            segment = ET.SubElement(track, f"{{{ns}}}trkseg")
        point = ET.SubElement(
            segment,
            f"{{{ns}}}trkpt",
            lat=str(session.channels["GPS_LAT"].samples[i]),
            lon=str(session.channels["GPS_LON"].samples[i]),
        )
        altitude = session.channels.get("GPS_ALT_MSL_M")
        if altitude is not None and altitude.valid[i] and np.isfinite(altitude.samples[i]):
            ET.SubElement(point, f"{{{ns}}}ele").text = str(altitude.samples[i])
        if tz:
            try:
                time = datetime.strptime(session.timestamp_raw[i], "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                pass
            else:
                # DST fold remains ambiguous in naive input: user must resolve it before export.
                if (
                    time.replace(tzinfo=tz, fold=0).utcoffset()
                    != time.replace(tzinfo=tz, fold=1).utcoffset()
                ):
                    raise ValueError(
                        "Ambiguous/nonexistent DST time; clear session timezone to export raw times"
                    )
                ET.SubElement(point, f"{{{ns}}}time").text = (
                    time.replace(tzinfo=tz).astimezone(UTC).isoformat().replace("+00:00", "Z")
                )
        ext = ET.SubElement(point, f"{{{ns}}}extensions")
        ET.SubElement(ext, f"{{{tv}}}timestamp_raw").text = session.timestamp_raw[i]
        ET.SubElement(ext, f"{{{tv}}}row_index").text = str(i)
        source = session.source_for_sample(int(i))
        if source is not None:
            ET.SubElement(ext, f"{{{tv}}}source_file").text = source.path.name
            if session.source_lines is not None:
                ET.SubElement(ext, f"{{{tv}}}source_line").text = str(session.source_lines[i])
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def export_estimated_gpx(session: Session, path: Path) -> None:
    """Separate, explicitly identified EST track. The raw-GPS exporter is unchanged."""
    if session.navigation is None:
        raise ValueError("Estimate vehicle motion first; this session has no EST trajectory")
    result = session.navigation
    lat, lon = result.channels["EST_LAT"], result.channels["EST_LON"]
    rows = np.flatnonzero(lat.valid & lon.valid)
    if not len(rows):
        raise ValueError("No GPS-initialized EST positions to export")
    ns, tv = "http://www.topografix.com/GPX/1/1", "https://telemetry-viewer.local/schema/1"
    ET.register_namespace("", ns)
    ET.register_namespace("tv", tv)
    root = ET.Element(f"{{{ns}}}gpx", version="1.1", creator="LapTune EST")
    track = ET.SubElement(root, f"{{{ns}}}trk")
    ET.SubElement(track, f"{{{ns}}}name").text = session.filename + " · EST estimated trajectory"
    ET.SubElement(track, f"{{{ns}}}desc").text = (
        "Estimated IMU/GNSS trajectory, including dead reckoning. Not raw GPS measurements. "
        "Uncertainty is model based. Vehicle axes X rear, Y right, Z up."
    )
    segment, previous = None, None
    for row in rows:
        if segment is None or result.segment_start[row] or previous is None or row != previous + 1:
            segment = ET.SubElement(track, f"{{{ns}}}trkseg")
        point = ET.SubElement(
            segment, f"{{{ns}}}trkpt", lat=str(lat.samples[row]), lon=str(lon.samples[row])
        )
        altitude = result.channels["EST_ALT_MSL_M"]
        if altitude.valid[row]:
            ET.SubElement(point, f"{{{ns}}}ele").text = str(altitude.samples[row])
        extension = ET.SubElement(point, f"{{{ns}}}extensions")
        ET.SubElement(extension, f"{{{tv}}}row_index").text = str(row)
        ET.SubElement(extension, f"{{{tv}}}timestamp_raw").text = session.timestamp_raw[row]
        for name in ("EST_NAV_STATUS", "EST_POS_95_M", "EST_TIME_S", "EST_TIME_QUALITY"):
            ET.SubElement(extension, f"{{{tv}}}{name}").text = str(
                result.channels[name].samples[row]
            )
        source = session.source_for_sample(int(row))
        if source is not None:
            ET.SubElement(extension, f"{{{tv}}}source_file").text = source.path.name
        previous = row
    extensions = ET.SubElement(root, f"{{{ns}}}extensions")
    ET.SubElement(extensions, f"{{{tv}}}EST_report").text = json.dumps(result.report)
    # Keep naive/missing raw time explicit; no synthetic UTC times are manufactured.
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
