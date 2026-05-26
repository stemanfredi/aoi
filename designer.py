"""Interactive design surface — ipyleaflet UI around `AOISession`.

`launch(...)` builds the widget tree and displays it. README.md
covers the workflow.
"""
from __future__ import annotations

import importlib
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from ipyleaflet import (Circle, DrawControl, FullScreenControl, ImageOverlay,
                        Map, Marker, Polygon, Polyline, ScaleControl, basemaps)
from ipywidgets import (BoundedFloatText, Button, Checkbox, Dropdown,
                        FloatText, HBox, HTML, Layout, Tab, Text, VBox)
from IPython.display import HTML as IPyHTML, display

# Shrink the leaflet-draw vertex / midpoint handles — defaults are ~20 px
# squares which dominate the map. 8 px reads as a marker, not a blocker.
_DRAW_HANDLE_CSS = """
<style>
.leaflet-div-icon, .leaflet-editing-icon {
    width: 8px !important;
    height: 8px !important;
    margin-left: -4px !important;
    margin-top: -4px !important;
    border: 1px solid #555 !important;
    border-radius: 2px !important;
}
</style>
"""

# `%run designer.py` re-imports this module, but `from render import …`
# returns the kernel's cached module. Force a fresh reload so edits to
# fetch / drawables / presets / render are picked up every run.
for _name in ("fetch", "drawables", "presets", "session", "render"):
    if _name in sys.modules:
        importlib.reload(sys.modules[_name])

from render import ChartStyle
from session import AOISession
from presets import PRESETS, list_presets
from drawables import (ActiveSonar, AreaPolygon, DistanceLine, PassiveArray,
                        PassiveNode, SurveySwath, _format_area, _format_distance)


ASPECTS: dict[str, float] = {
    "3:2  landscape":  3 / 2,
    "4:3  landscape":  4 / 3,
    "16:9 widescreen": 16 / 9,
    "1:1  square":     1.0,
    "A4   landscape":  297 / 210,
    "A4   portrait":   210 / 297,
    "2:3  portrait":   2 / 3,
}
DEFAULT_ASPECT = "3:2  landscape"
MAP_WIDTH_PX   = 900
SAVE_WIDTH_IN  = 15
SAVE_DPI       = 220


# Leaflet-preview colours (the rendered PNG uses each sensor's own .color)
_SENSOR_PREVIEW = {
    "ActiveSonar":  {"color": "#c0392b", "weight": 2, "dash_array": "6,3"},
    "PassiveNode":  {"color": "#2980b9", "weight": 2, "dash_array": "6,3"},
    "PassiveArray": {"color": "#2980b9", "weight": 1, "dash_array": None},
    "SurveySwath":  {"color": "#16a085", "weight": 2, "dash_array": "6,3"},
}


# ── _Placement: one sensor on the design surface ──────────────────────────
@dataclass
class _Placement:
    """A placed sensor with its live Leaflet handles."""
    preset_name: str
    label: str
    type: str
    color: str
    # ActiveSonar anchor
    lat: float = 0.0
    lon: float = 0.0
    range_m: float = 0
    bearing_deg: float = 0
    beam_width_deg: float = 360
    # PassiveArray nodes
    nodes: List[tuple] = field(default_factory=list)
    # SurveySwath waypoints
    track: List[tuple] = field(default_factory=list)
    swath_m: float = 0
    # Live ipyleaflet handles (skipped in any future serialisation)
    _markers: list = field(default_factory=list)
    _coverage: list = field(default_factory=list)


# ── Geometry helpers (for Leaflet preview only) ────────────────────────────
EARTH_R = 6_378_137.0


def _destination(lat, lon, bearing_deg, distance_m):
    """Great-circle destination from (lat, lon)."""
    br = math.radians(bearing_deg)
    d  = distance_m / EARTH_R
    lat1, lon1 = math.radians(lat), math.radians(lon)
    lat2 = math.asin(math.sin(lat1)*math.cos(d) +
                     math.cos(lat1)*math.sin(d)*math.cos(br))
    lon2 = lon1 + math.atan2(math.sin(br)*math.sin(d)*math.cos(lat1),
                              math.cos(d) - math.sin(lat1)*math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)


def _wedge_latlon(lat, lon, range_m, bearing_deg, beam_width_deg, n=48):
    half = beam_width_deg / 2.0
    angles = [bearing_deg - half + (beam_width_deg * i / (n - 1))
              for i in range(n)]
    arc = [_destination(lat, lon, a, range_m) for a in angles]
    return [(lat, lon)] + arc + [(lat, lon)]


def _placement_from_preset(preset_name: str, lat: float, lon: float) -> _Placement:
    """Build a _Placement from a registry entry."""
    p = PRESETS[preset_name]
    out = _Placement(preset_name=preset_name, label=preset_name,
                     type=p["type"], color=p.get("color", "#c0392b"),
                     lat=lat, lon=lon)
    if p["type"] == "ActiveSonar":
        out.range_m = float(p.get("range_m", 1000))
        out.beam_width_deg = float(p.get("beam_width_deg", 360))
        out.bearing_deg = float(p.get("bearing_deg", 0))
    elif p["type"] == "PassiveNode":
        out.range_m = float(p.get("range_m", 2000))
    elif p["type"] == "PassiveArray":
        n = int(p.get("nodes_default", 4))
        s = float(p.get("spacing_m_default", 400))
        side = int(math.ceil(math.sqrt(n)))
        nodes = []
        for i in range(side):
            for j in range(side):
                if len(nodes) >= n: break
                dy = (i - (side - 1) / 2) * s
                dx = (j - (side - 1) / 2) * s
                dlat = dy / 110_540
                dlon = dx / (111_320 * math.cos(math.radians(lat)))
                nodes.append((lat + dlat, lon + dlon))
        out.nodes = nodes
    elif p["type"] == "SurveySwath":
        out.swath_m = float(p.get("swath_m_default", 400))
        out.track = [(lat - 0.005, lon), (lat + 0.005, lon)]
    return out


def _to_engine_sensor(p: _Placement):
    """Convert a _Placement to the corresponding _sensors archetype."""
    if p.type == "ActiveSonar":
        return ActiveSonar(center=(p.lat, p.lon), range_m=p.range_m,
                           label=p.label,
                           bearing_deg=(p.bearing_deg
                                        if p.beam_width_deg < 360 else None),
                           beam_width_deg=p.beam_width_deg, color=p.color)
    if p.type == "PassiveNode":
        return PassiveNode(center=(p.lat, p.lon), range_m=p.range_m,
                           label=p.label, color=p.color)
    if p.type == "PassiveArray":
        return PassiveArray(nodes=list(p.nodes), label=p.label, color=p.color)
    if p.type == "SurveySwath":
        return SurveySwath(track=list(p.track), swath_m=p.swath_m,
                           label=p.label, color=p.color)
    return None


# ── Measurements ───────────────────────────────────────────────────────────

@dataclass
class _Measurement:
    """A user-drawn distance line or area polygon (lat/lon vertices)."""
    kind: str                              # "DistanceLine" or "AreaPolygon"
    vertices: List[tuple]                  # [(lat, lon), ...]


def _to_engine_measurement(m: _Measurement):
    """Convert a _Measurement to the corresponding sensors.* drawable."""
    if m.kind == "DistanceLine":
        return DistanceLine(vertices=list(m.vertices))
    if m.kind == "AreaPolygon":
        return AreaPolygon(vertices=list(m.vertices))
    return None


def _live_metric(vertices: List[tuple], kind: str) -> float:
    """Length (m) or area (m²) computed in a local TMerc at the geometry's
    centroid. Used for the live readout in the UI list — the rendered
    chart re-projects to its own CRS, but values are within metres of each
    other for AOI-scale geometries."""
    if not vertices:
        return 0.0
    from pyproj import Transformer
    avg_lat = sum(v[0] for v in vertices) / len(vertices)
    avg_lon = sum(v[1] for v in vertices) / len(vertices)
    crs = (f"+proj=tmerc +lat_0={avg_lat} +lon_0={avg_lon} +k=1 "
           f"+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")
    fwd = Transformer.from_crs(4326, crs, always_xy=True)
    xys = [fwd.transform(lon, lat) for lat, lon in vertices]
    if kind == "DistanceLine" and len(xys) >= 2:
        return sum(((xys[i+1][0]-xys[i][0])**2 + (xys[i+1][1]-xys[i][1])**2)**0.5
                   for i in range(len(xys)-1))
    if kind == "AreaPolygon" and len(xys) >= 3:
        from shapely.geometry import Polygon as ShpPolygon
        return ShpPolygon(xys).area
    return 0.0


# ── UI ─────────────────────────────────────────────────────────────────────

def launch(center: Tuple[float, float] = (41.9028, 12.4964),   # Rome — Europe-level
           zoom: int = 4,
           aspect: str = DEFAULT_ASPECT,
           name: str = "designer",
           title: Optional[str] = None,
           output_dir: Optional[Path] = None):
    """Build and display the chart-designer UI."""
    if aspect not in ASPECTS:
        raise ValueError(f"unknown aspect {aspect!r}; choose from {list(ASPECTS)}")

    placements:   List[_Placement]   = []
    measurements: List[_Measurement] = []

    # ── Map widget at the chosen aspect ────────────────────────────────────
    def _pin_layout(w_px: int, h_px: int) -> Layout:
        """Pin exact dimensions — no flex grow/shrink (so neither HBox
        squeezing nor browser zoom can break the aspect ratio)."""
        return Layout(width=f"{w_px}px", height=f"{h_px}px",
                      min_width=f"{w_px}px", min_height=f"{h_px}px",
                      flex="0 0 auto")

    def _map_dims(aspect_key: str) -> Tuple[int, int]:
        return MAP_WIDTH_PX, int(round(MAP_WIDTH_PX / ASPECTS[aspect_key]))

    map_w, map_h = _map_dims(aspect)
    m = Map(basemap=basemaps.OpenStreetMap.Mapnik, center=center, zoom=zoom,
            scroll_wheel_zoom=True, layout=_pin_layout(map_w, map_h))
    m.add_control(ScaleControl(position="bottomleft"))
    m.add_control(FullScreenControl())

    # Draw tools for measurements (polyline → distance, polygon → area).
    # Other shapes disabled — sensors have their own preset-driven UI in
    # the Sensors tab.
    draw_control = DrawControl(
        polyline={"shapeOptions": {"color": "#34495e", "weight": 2}},
        polygon={"shapeOptions": {"color": "#34495e", "weight": 2,
                                   "fillOpacity": 0.12}},
        rectangle={}, circle={}, marker={}, circlemarker={},
        edit=True, remove=True,
    )
    m.add_control(draw_control)

    aspect_dd = Dropdown(options=list(ASPECTS), value=aspect, description="")
    title_input = Text(value=title or "",
                        placeholder="Leave blank for none",
                        description="",
                        layout=Layout(width="420px"),
                        continuous_update=False)
    status    = HTML(value="")

    # Captured frame state — set by Preview, consumed by Save so the
    # written PNG always matches the on-screen overlay.
    frame_state: dict = {}

    def _invalidate_preview(msg=""):
        """Remove the bathy overlay and reset the captured frame."""
        ov = frame_state.get("overlay")
        if ov is not None and ov in m.layers:
            m.remove(ov)
        frame_state.clear()
        if msg:
            status.value = msg

    def _on_aspect(change):
        w, h = _map_dims(change["new"])
        m.layout.width      = f"{w}px"
        m.layout.height     = f"{h}px"
        m.layout.min_width  = f"{w}px"
        m.layout.min_height = f"{h}px"
        # Nudge center to force Leaflet to invalidate its internal viewport
        c = m.center
        m.center = [c[0] + 1e-9, c[1]]
        m.center = list(c)
        # A new aspect changes the frame geometry; any preview overlay
        # is now stale.
        _invalidate_preview(
            "<i>Aspect changed — preview cleared. Click Preview to refresh.</i>"
        )
    aspect_dd.observe(_on_aspect, names="value")

    # ── Sensor placement ──────────────────────────────────────────────────
    preset_dd = Dropdown(options=list_presets(),
                          description="Preset:",
                          layout=Layout(width="380px"))
    add_centre_btn = Button(description="Add at map centre",
                             button_style="primary", icon="plus",
                             layout=Layout(width="200px"))
    coord_lat = FloatText(value=center[0], description="lat:",
                           layout=Layout(width="180px"))
    coord_lon = FloatText(value=center[1], description="lon:",
                           layout=Layout(width="180px"))
    add_coords_btn = Button(description="Add at lat / lon",
                             icon="plus", layout=Layout(width="200px"))

    list_box = VBox(layout=Layout(padding="2px", max_height="380px",
                                   overflow_y="auto"))

    def _build_coverage(p: _Placement) -> list:
        s = _SENSOR_PREVIEW[p.type]
        if p.type == "ActiveSonar":
            if p.beam_width_deg >= 360:
                return [Circle(location=(p.lat, p.lon), radius=int(p.range_m),
                                color=s["color"], fill_color=s["color"],
                                fill_opacity=0.12, weight=s["weight"],
                                dash_array=s["dash_array"])]
            verts = _wedge_latlon(p.lat, p.lon, p.range_m,
                                   p.bearing_deg, p.beam_width_deg)
            return [Polygon(locations=verts, color=s["color"],
                             fill_color=s["color"], fill_opacity=0.12,
                             weight=s["weight"], dash_array=s["dash_array"])]
        if p.type == "PassiveNode":
            return [Circle(location=(p.lat, p.lon), radius=int(p.range_m),
                            color=s["color"], fill_color=s["color"],
                            fill_opacity=0.12, weight=s["weight"],
                            dash_array=s["dash_array"])]
        if p.type == "SurveySwath":
            return [Polyline(locations=list(p.track), color=s["color"],
                              weight=s["weight"])]
        return []

    def _build_markers(p: _Placement) -> list:
        if p.type in ("ActiveSonar", "PassiveNode"):
            mk = Marker(location=(p.lat, p.lon), draggable=True, title=p.label)
            def _on_move(change, p=p):
                p.lat, p.lon = change["new"][0], change["new"][1]
                _redraw_coverage(p)
            mk.observe(_on_move, names="location")
            return [mk]
        if p.type == "PassiveArray":
            markers = []
            for idx, (la, lo) in enumerate(p.nodes):
                mk = Marker(location=(la, lo), draggable=True,
                            title=f"{p.label} #{idx+1}")
                def _on_n(change, p=p, idx=idx):
                    p.nodes[idx] = (change["new"][0], change["new"][1])
                mk.observe(_on_n, names="location")
                markers.append(mk)
            return markers
        if p.type == "SurveySwath":
            markers = []
            for idx, (la, lo) in enumerate(p.track):
                mk = Marker(location=(la, lo), draggable=True,
                            title=f"{p.label} wp{idx+1}")
                def _on_wp(change, p=p, idx=idx):
                    p.track[idx] = (change["new"][0], change["new"][1])
                    _redraw_coverage(p)
                mk.observe(_on_wp, names="location")
                markers.append(mk)
            return markers
        return []

    def _redraw_coverage(p: _Placement):
        for layer in p._coverage:
            if layer in m.layers: m.remove(layer)
        p._coverage = _build_coverage(p)
        for layer in p._coverage:
            m.add(layer)

    def _register_placement(p: _Placement):
        p._markers = _build_markers(p)
        for mk in p._markers: m.add(mk)
        p._coverage = _build_coverage(p)
        for layer in p._coverage: m.add(layer)
        placements.append(p)
        _refresh_list()

    def _remove_placement(p: _Placement):
        for layer in list(p._coverage) + list(p._markers):
            if layer in m.layers: m.remove(layer)
        placements.remove(p)
        _refresh_list()

    def _make_card(p: _Placement):
        """Build an editable card for one placement (header + attribute fields)."""
        header = HTML(value=(f"<b>{p.label}</b><br>"
                             f"<span style='color:#777;font-size:0.8em'>"
                             f"{p.type}</span>"))
        rm = Button(description="Remove",
                    layout=Layout(width="78px"))
        rm.on_click(lambda b, p=p: _remove_placement(p))
        top = HBox([header, rm], layout=Layout(justify_content="space-between"))

        fld_layout = Layout(width="170px")
        fields = []

        if p.type == "ActiveSonar":
            range_w = BoundedFloatText(value=p.range_m, min=50, max=10000,
                                       step=50, description="range (m)",
                                       continuous_update=False, layout=fld_layout)
            beam_w  = BoundedFloatText(value=p.beam_width_deg, min=10, max=360,
                                       step=5, description="beam (°)",
                                       continuous_update=False, layout=fld_layout)
            bear_w  = BoundedFloatText(value=p.bearing_deg, min=0, max=359,
                                       step=5, description="bearing (°)",
                                       continuous_update=False, layout=fld_layout)
            def _on_change(_change, p=p, range_w=range_w, beam_w=beam_w,
                           bear_w=bear_w):
                p.range_m = float(range_w.value)
                p.beam_width_deg = float(beam_w.value)
                p.bearing_deg = float(bear_w.value)
                _redraw_coverage(p)
            for w in (range_w, beam_w, bear_w):
                w.observe(_on_change, names="value")
            fields = [HBox([range_w, beam_w]),
                      HBox([bear_w, HTML("<small style='color:#888'>"
                                          "drag marker to reposition</small>")])]

        elif p.type == "PassiveNode":
            range_w = BoundedFloatText(value=p.range_m, min=50, max=50000,
                                       step=100, description="range (m)",
                                       continuous_update=False, layout=fld_layout)
            def _on_range(_change, p=p, w=range_w):
                p.range_m = float(w.value)
                _redraw_coverage(p)
            range_w.observe(_on_range, names="value")
            fields = [HBox([range_w, HTML("<small style='color:#888'>"
                                           "drag marker to reposition</small>")])]

        elif p.type == "PassiveArray":
            fields = [HTML(value=(f"<small style='color:#666'>"
                                  f"{len(p.nodes)} nodes — "
                                  f"drag markers to reposition</small>"))]

        elif p.type == "SurveySwath":
            swath_w = BoundedFloatText(value=p.swath_m, min=10, max=5000,
                                       step=10, description="swath (m)",
                                       continuous_update=False, layout=fld_layout)
            def _on_swath(_change, p=p, w=swath_w):
                p.swath_m = float(w.value)
                _redraw_coverage(p)
            swath_w.observe(_on_swath, names="value")
            fields = [HBox([swath_w, HTML("<small style='color:#888'>"
                                          f"{len(p.track)} waypoints — drag to "
                                          f"reposition</small>")])]

        return VBox([top] + fields,
                    layout=Layout(border="1px solid #e0e0e0",
                                   border_radius="4px",
                                   padding="6px 8px", margin="4px 0"))

    def _refresh_list():
        if not placements:
            list_box.children = (HTML(value=(
                "<i style='color:#888'>No sensors yet — pick a preset "
                "above and click <b>Add at map centre</b>.</i>")),)
            return
        list_box.children = tuple(_make_card(p) for p in placements)

    _refresh_list()

    def _add_at_centre(_b=None):
        c = m.center
        _register_placement(_placement_from_preset(preset_dd.value, c[0], c[1]))

    def _add_at_coords(_b=None):
        try:
            _register_placement(_placement_from_preset(
                preset_dd.value, float(coord_lat.value), float(coord_lon.value)))
        except Exception as exc:
            status.value = f"<b style='color:#c0392b'>{exc}</b>"

    add_centre_btn.on_click(_add_at_centre)
    add_coords_btn.on_click(_add_at_coords)

    # ── Measurements — DrawControl ↔ measurements list sync ───────────────
    # Single source of truth: draw_control.data (the GeoJSON
    # FeatureCollection the user has drawn). Our `measurements` list is
    # a derived mirror, rebuilt on every change so create / edit / remove
    # via the DrawControl's toolbar all flow through one path.
    measure_list_box = VBox(layout=Layout(padding="2px", max_height="320px",
                                           overflow_y="auto"))

    def _measure_row(idx: int, m: _Measurement):
        val_m = _live_metric(m.vertices, m.kind)
        if m.kind == "DistanceLine":
            badge, val = "↔", _format_distance(val_m)
        else:
            badge, val = "▱", _format_area(val_m)
        return HTML(value=(f"<div style='padding:4px 8px;"
                            f"border:1px solid #e0e0e0;border-radius:4px;"
                            f"margin:3px 0'>"
                            f"<b style='color:#34495e'>{badge} M{idx+1}</b> "
                            f"<span style='color:#222'>{val}</span> "
                            f"<small style='color:#888'>· "
                            f"{len(m.vertices)} pt(s)</small></div>"))

    def _refresh_measure_list():
        if not measurements:
            measure_list_box.children = (HTML(value=(
                "<i style='color:#888'>No measurements yet — use the "
                "polyline (↔) or polygon (▱) tools on the map. "
                "Edit / remove via the map toolbar.</i>")),)
            return
        measure_list_box.children = tuple(
            _measure_row(i, m) for i, m in enumerate(measurements))

    def _on_draw_data(change):
        """Rebuild `measurements` from the current DrawControl feature set."""
        measurements.clear()
        for feat in change["new"]:
            g = feat.get("geometry", {})
            t = g.get("type")
            if t == "LineString":
                verts = [(lat, lon) for lon, lat in g["coordinates"]]
                if len(verts) >= 2:
                    measurements.append(_Measurement("DistanceLine", verts))
            elif t == "Polygon":
                # GeoJSON polygons repeat the first vertex at the end; drop it
                ring = g["coordinates"][0]
                verts = [(lat, lon) for lon, lat in ring[:-1]]
                if len(verts) >= 3:
                    measurements.append(_Measurement("AreaPolygon", verts))
        _refresh_measure_list()

    draw_control.observe(_on_draw_data, names="data")
    _refresh_measure_list()

    # ── Render ────────────────────────────────────────────────────────────
    # ── Layers panel — visibility toggles wired to ChartStyle ─────────────
    # Each checkbox maps to a ChartStyle field; defaults match the engine's.
    # Layer toggles, grouped semantically. Each (section_label, [(label,
    # ChartStyle field, default), ...]) — designer renders one VBox per
    # section so the 15 checkboxes read as 3 chunks, not one long list.
    _layer_cb_layout = Layout(width="auto", margin="0 0 0 8px")
    _layer_groups = [
        ("Sea", [
            ("Bathymetric lines",           "show_isobaths",            True),
            ("Bathymetric labels",          "show_isobath_labels",      True),
        ]),
        ("Land", [
            ("Terrain contours",            "show_terrain_contours",    True),
            ("Terrain labels",              "show_terrain_labels",      True),
            ("Coastline",                   "show_coastline",           True),
            ("Place names",                 "show_place_names",         True),
            ("Buildings / streets / piers", "show_port_features",       True),
        ]),
        ("Chart frame", [
            ("Graticule",                   "show_graticule",           True),
            ("Tick labels (lat/lon)",       "show_tick_labels",         True),
            ("North arrow",                 "show_north_arrow",         True),
            ("Scale bar (chequered)",       "show_scale_bar",           True),
            ("Scale ratio (1:N)",           "show_scale_ratio",         False),
            ("Bottom caption",              "show_caption",             True),
            ("Title",                       "show_title",               True),
        ]),
        ("Emphasis", [
            ("Emphasise major contours",    "emphasize_major_contours", True),
        ]),
    ]
    _layer_widgets = {
        field: Checkbox(value=default, description=label, indent=False,
                        layout=_layer_cb_layout)
        for _, items in _layer_groups for (label, field, default) in items
    }

    preview_btn = Button(description="Preview bathy",
                          button_style="info", icon="globe",
                          layout=Layout(width="170px"))
    save_btn    = Button(description="Save PNG",
                          button_style="success", icon="image",
                          layout=Layout(width="170px"))

    PREVIEW_DIR = Path(__file__).resolve().parent / "cache"
    PREVIEW_FILENAME = "preview.png"
    PREVIEW_DPI = 100

    # One AOISession is shared by Preview and Save — Preview populates
    # the cache; Save renders against it (no re-fetch, no re-projection).
    # Frame-changing updates auto-invalidate the cache; frame-preserving
    # changes (style toggles, drawable adds/removes) do not.
    session: dict = {"obj": AOISession(name=name, center=center,
                                        half_w_km=1.0, aspect=2/3)}

    def _capture_frame():
        """Snapshot the Leaflet map's current view as engine frame params.

        Returns (center, half_w_km, aspect, bounds_for_overlay) or None
        if the map widget hasn't measured itself yet.
        """
        b = m.bounds
        if not b:
            return None
        (s, w), (n, e) = b
        c_lat, c_lon = (s + n) / 2, (w + e) / 2
        width_km = abs(e - w) * 111.32 * math.cos(math.radians(c_lat))
        ratio_wh = ASPECTS[aspect_dd.value]
        return {
            "center":    (c_lat, c_lon),
            "half_w_km": width_km / 2,
            "aspect":    1.0 / ratio_wh,
            "bounds":    ((s, w), (n, e)),
        }

    def _sync_session(frame):
        """Push the captured frame into the shared AOISession; if the
        frame moved, the cache is auto-dropped and the next render will
        re-fetch."""
        session["obj"].update(
            center=frame["center"],
            half_w_km=frame["half_w_km"],
            aspect=frame["aspect"],
        )

    def _build_style() -> ChartStyle:
        return ChartStyle(**{field: w.value for field, w in _layer_widgets.items()})

    def _overlay_url(path: Path) -> str:
        """Base64 data URI — works in lab / classic / remote kernels."""
        import base64
        return ("data:image/png;base64,"
                + base64.b64encode(path.read_bytes()).decode())

    def _on_preview(_=None):
        frame = _capture_frame()
        if frame is None:
            status.value = ("<b style='color:#c0392b'>map bounds not ready — "
                             "pan or zoom once first.</b>")
            return
        status.value = "<i>Fetching + rendering preview underlay…</i>"
        _sync_session(frame)
        PREVIEW_DIR.mkdir(exist_ok=True)
        try:
            preview_w_in = MAP_WIDTH_PX / PREVIEW_DPI
            preview_h_in = preview_w_in * frame["aspect"]
            out = session["obj"].render(
                drawables=[], title=None,
                style=_build_style(),
                figsize=(preview_w_in, preview_h_in), dpi=PREVIEW_DPI,
                output_dir=PREVIEW_DIR,
            )
            preview_path = PREVIEW_DIR / PREVIEW_FILENAME
            if preview_path.exists():
                preview_path.unlink()
            out.rename(preview_path)
        except Exception as exc:
            status.value = f"<b style='color:#c0392b'>preview failed: {exc}</b>"
            return
        _invalidate_preview()      # remove any previous overlay
        overlay = ImageOverlay(url=_overlay_url(preview_path),
                                bounds=frame["bounds"], opacity=1.0)
        m.add(overlay)
        frame_state.update(frame)
        frame_state["overlay"] = overlay
        status.value = ("<b style='color:#27ae60'>preview loaded.</b> "
                         "Place / drag sensors on the chart, then Save.")

    def _on_save(_=None):
        # Use the captured preview frame so the saved PNG matches the
        # on-screen overlay even if the user panned afterwards. If
        # Preview was never clicked, fall back to live map bounds.
        if "center" in frame_state:
            frame = {k: frame_state[k] for k in ("center", "half_w_km", "aspect")}
        else:
            cap = _capture_frame()
            if cap is None:
                status.value = ("<b style='color:#c0392b'>map bounds not ready"
                                 " — pan or zoom once first.</b>")
                return
            frame = {k: cap[k] for k in ("center", "half_w_km", "aspect")}
        _sync_session(frame)

        drawables = [s for s in (_to_engine_sensor(p) for p in placements)
                     if s is not None]
        drawables += [d for d in (_to_engine_measurement(m_)
                                   for m_ in measurements) if d is not None]
        n_real = len(placements)
        n_meas = len(measurements)
        figsize = (SAVE_WIDTH_IN, SAVE_WIDTH_IN * frame["aspect"])
        status.value = (f"<i>Saving — {n_real} sensor(s), "
                         f"{n_meas} measurement(s)…</i>")
        try:
            path = session["obj"].render(
                drawables=drawables,
                title=title_input.value or None,
                style=_build_style(),
                figsize=figsize, dpi=SAVE_DPI,
                output_dir=output_dir,
            )
        except Exception as exc:
            status.value = f"<b style='color:#c0392b'>save failed: {exc}</b>"
            return
        status.value = (f"<b style='color:#27ae60'>saved.</b> "
                         f"<code>{path}</code>")

    preview_btn.on_click(_on_preview)
    save_btn.on_click(_on_save)

    # ── Tab 1: Map (frame + layers) ────────────────────────────────────────
    _section_kids = []
    for section_label, items in _layer_groups:
        _section_kids.append(HTML(
            value=f"<small style='color:#666'><b>{section_label}</b></small>"))
        for (_, field, _default) in items:
            _section_kids.append(_layer_widgets[field])
    layers_box = VBox(
        _section_kids,
        layout=Layout(border="1px solid #ddd", padding="6px",
                      max_height="380px", overflow_y="auto"),
    )
    map_tab = VBox([
        HTML("<b>Title</b>"),
        title_input,
        HTML("<b>Frame</b>"),
        aspect_dd,
        HTML("<b>Layers</b>"),
        layers_box,
    ], layout=Layout(padding="8px"))

    # ── Tab: Measure (distance + area, via the map's draw toolbar) ────────
    measure_tab = VBox([
        HTML("<b>Distance &amp; area</b><br>"
              "<small>Use the polyline ↔ or polygon ▱ tools on the map. "
              "Values shown below update live; remove via the map "
              "toolbar's edit / delete buttons.</small>"),
        measure_list_box,
    ], layout=Layout(padding="8px"))

    # ── Tab: Sensors (library + placed list with editable attributes) ────
    sensors_tab = VBox([
        HTML("<b>Library</b><br>"
              "<small>Pick a preset, then place it on the map.</small>"),
        preset_dd, add_centre_btn,
        HBox([coord_lat, coord_lon]),
        add_coords_btn,
        HTML("<hr><b>Placed sensors</b><br>"
              "<small>Drag markers to move; edit attributes inline.</small>"),
        list_box,
    ], layout=Layout(padding="8px"))

    tabs = Tab(children=[map_tab, measure_tab, sensors_tab],
               layout=Layout(width="100%"))
    tabs.set_title(0, "Map")
    tabs.set_title(1, "Measure")
    tabs.set_title(2, "Sensors")

    # ── Footer (always shown) — Preview + Save + status ───────────────────
    footer = VBox([
        HBox([preview_btn, save_btn]),
        status,
    ], layout=Layout(border="1px solid #ddd", padding="8px",
                     margin="6px 0 0 0", background_color="#fafafa"))

    controls = VBox([tabs, footer],
                    layout=Layout(width="420px", min_width="420px",
                                   padding="0", flex="0 0 auto"))

    display(IPyHTML(_DRAW_HANDLE_CSS))
    display(HBox([m, controls],
                 layout=Layout(align_items="flex-start",
                               flex_flow="row nowrap")))
