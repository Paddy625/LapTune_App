import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from telemetry_viewer.domain.models import Session
from telemetry_viewer.processing.gps import haversine_m
from telemetry_viewer.processing.map_range import RouteRange


class Bridge(QObject):
    location = Signal(float, float)
    loaded = Signal()

    @Slot(float, float)
    def locationClicked(self, lat, lon):
        self.location.emit(lat, lon)

    @Slot()
    def ready(self):
        self.loaded.emit()


class LocalPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return not is_main_frame or url.isLocalFile() or url.scheme() in ("qrc", "about")


class MapWidget(QWidget):
    location_selected = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.is_ready = False
        self.current_sample = 0
        self.marker_row = None
        self.visible_range = None
        self.range_locator = None
        self._last_range = None
        self.estimated_range_locator = None
        self.estimated_marker_row = None
        self.track_mode = "GPS + EST"
        self.layer = ("Road", "", "")
        self.view = QWebEngineView(self)
        # Memory-only profile: private tracks and credentials are not put in a web disk cache.
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpUserAgent(
            "TelemetryViewer/0.1 (https://github.com/Paddy625/LapTune_App) QtWebEngine"
        )
        self.page = LocalPage(self.profile, self.view)
        self.view.setPage(self.page)
        self.page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        self.page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        self.channel = QWebChannel(self.page)
        self.bridge = Bridge(self.channel)
        self.bridge.location.connect(self.location_selected)
        self.bridge.loaded.connect(self._ready)
        self.channel.registerObject("bridge", self.bridge)
        self.page.setWebChannel(self.channel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.view.setUrl(QUrl.fromLocalFile(str(Path(__file__).parent / "assets" / "map.html")))

    def _ready(self):
        self.is_ready = True
        self.set_layer(*self.layer)
        if self.session is not None:
            self.set_session(self.session)
            self.set_cursor(self.current_sample)

    def set_session(self, session: Session):
        if self.session is not session:
            self.visible_range = None
        self.range_locator = RouteRange(session)
        self._last_range = None
        self.session = session
        self.marker_row = None
        self.estimated_marker_row = None
        self.estimated_range_locator = None
        if not self.is_ready:
            return
        segments = []
        previous = None
        for row, starts in zip(session.gps_fix_samples, session.gps_segment_start, strict=True):
            if starts or not segments:
                segments.append([])
                previous = None
            point = (
                float(session.channels["GPS_LAT"].samples[row]),
                float(session.channels["GPS_LON"].samples[row]),
            )
            # Speed may refresh faster than coordinates. Keep source fixes intact, but
            # drawing the same geographic vertex repeatedly adds no route detail.
            if point != previous:
                segments[-1].append(point)
                previous = point
        self.page.runJavaScript("setRoute(" + json.dumps(segments, allow_nan=False) + ")")
        if session.navigation is not None:
            self.set_estimated_route()
        self.set_cursor(0)
        self._send_visible_range()

    def set_visible_range(self, lo, hi, mode):
        self.visible_range = (float(lo), float(hi), mode)
        self._send_visible_range()

    def _send_visible_range(self):
        if not self.is_ready or self.range_locator is None or self.visible_range is None:
            return
        locator = self.estimated_range_locator if self.track_mode == "EST" else self.range_locator
        marks = locator.locate(*self.visible_range) if locator is not None else None
        encoded = json.dumps(marks, allow_nan=False)
        if encoded != self._last_range:
            self.page.runJavaScript("setVisibleRange(" + encoded + ")")
            self._last_range = encoded

    def set_cursor(self, sample):
        self.current_sample = sample
        if self.session is None or not self.is_ready:
            return
        row = self.session.fix_for_sample(sample)
        if row is not None and row != self.marker_row:
            lat = self.session.channels["GPS_LAT"].samples[row]
            lon = self.session.channels["GPS_LON"].samples[row]
            if np.isfinite([lat, lon]).all():
                self.marker_row = row
                self.page.runJavaScript(f"setMarker({float(lat)}, {float(lon)}, {row})")
        navigation = self.session.navigation
        if navigation is not None and sample != self.estimated_marker_row:
            channels = navigation.channels
            self.estimated_marker_row = sample
            if channels["EST_LAT"].valid[sample] and channels["EST_LON"].valid[sample]:
                values = [
                    float(channels[name].samples[sample])
                    for name in ("EST_LAT", "EST_LON", "EST_POS_95_M", "EST_NAV_STATUS")
                ]
                self.page.runJavaScript(
                    "setEstimatedMarker(" + ",".join(json.dumps(v) for v in values) + f",{sample})"
                )
            else:
                self.page.runJavaScript("setEstimatedMarker(null)")

    def set_estimated_route(self):
        if self.session is None or self.session.navigation is None:
            return
        navigation = self.session.navigation
        lat, lon = (navigation.channels[name] for name in ("EST_LAT", "EST_LON"))
        rows = np.flatnonzero(lat.valid & lon.valid)
        starts = navigation.segment_start[rows].copy()
        if len(starts):
            starts[0] = True
        starts[1:] |= np.diff(rows) != 1
        proxy = replace(
            self.session,
            channels={**self.session.channels, "GPS_LAT": lat, "GPS_LON": lon},
            gps_fix_samples=rows,
            gps_segment_start=starts,
        )
        self.estimated_range_locator = RouteRange(proxy)
        self._last_range = None
        self.estimated_marker_row = None
        if not self.is_ready:
            return
        stride = max(1, int(np.ceil(len(rows) / 20000)))
        parts, current = [], None
        previous = None
        for j, row in enumerate(rows):
            status = int(navigation.channels["EST_NAV_STATUS"].samples[row])
            point = [float(lat.samples[row]), float(lon.samples[row])]
            if starts[j] or current is None or current["status"] != status:
                if current is not None and previous is not None and not starts[j]:
                    if not current["points"] or current["points"][-1] != previous:
                        current["points"].append(previous)
                current = {"status": status, "points": []}
                if previous is not None and not starts[j]:
                    current["points"].append(previous)
                parts.append(current)
            if (
                j % stride == 0
                or starts[j]
                or j == len(rows) - 1
                or (j + 1 < len(rows) and starts[j + 1])
            ):
                current["points"].append(point)
            previous = point
        self.page.runJavaScript("setEstimatedRoute(" + json.dumps(parts, allow_nan=False) + ")")
        self.set_track_mode(self.track_mode)
        self.set_cursor(self.current_sample)

    def set_track_mode(self, mode):
        self.track_mode = mode
        self._last_range = None
        if self.is_ready:
            self.page.runJavaScript("setTrackMode(" + json.dumps(mode) + ")")
            self._send_visible_range()

    def sample_for_location(self, lat, lon, active):
        raw = self.session.sample_for_location(lat, lon, active)
        navigation = self.session.navigation
        if navigation is None or self.track_mode == "GPS":
            return raw
        channels = navigation.channels
        rows = np.flatnonzero(channels["EST_LAT"].valid & channels["EST_LON"].valid)
        if not len(rows):
            return raw if self.track_mode != "EST" else None
        distances = haversine_m(
            lat, lon, channels["EST_LAT"].samples[rows], channels["EST_LON"].samples[rows]
        )
        candidates = rows[distances <= distances.min() + 5]
        selected = int(
            candidates[
                np.argmin(
                    abs(
                        self.session.elapsed_time_s[candidates]
                        - self.session.elapsed_time_s[active]
                    )
                )
            ]
        )
        if raw is None or self.track_mode == "EST":
            return selected
        raw_distance = haversine_m(
            lat,
            lon,
            self.session.channels["GPS_LAT"].samples[raw],
            self.session.channels["GPS_LON"].samples[raw],
        )
        return selected if distances.min() < raw_distance else raw

    def set_layer(self, mode, url="", attribution=""):
        self.layer = mode, url, attribution
        if self.is_ready:
            self.page.runJavaScript("setLayer(" + ",".join(json.dumps(x) for x in self.layer) + ")")
