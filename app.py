"""AOI — Streamlit UI for coastal sensor-siting charts.

Runs alongside `designer.ipynb`; both wrap the same engine
(`session.AOISession` + `render.coastal_chart`). The notebook gives a
richer in-Jupyter experience (drag markers); this Streamlit app trades
that for a web-app deployment story and a clean rerun model.

    streamlit run app.py
"""
from __future__ import annotations

import base64
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import folium
import streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium

from drawables import (ActiveSonar, AreaPolygon, DistanceLine, PassiveArray,
                       PassiveNode, SurveySwath, _format_area, _format_distance)
from presets import PRESETS
from render import ChartStyle
from session import AOISession


# ── Constants ──────────────────────────────────────────────────────────────

ASPECTS = {
    "3:2  landscape":  3 / 2,
    "4:3  landscape":  4 / 3,
    "16:9 widescreen": 16 / 9,
    "1:1  square":     1.0,
    "A4   landscape":  297 / 210,
    "A4   portrait":   210 / 297,
    "2:3  portrait":   2 / 3,
}
DEFAULT_ASPECT = "3:2  landscape"
DEFAULT_CENTER = (41.9028, 12.4964)    # Rome
DEFAULT_ZOOM   = 4
MAP_WIDTH_PX   = 1000
PREVIEW_DPI    = 100
SAVE_DPI       = 220
SAVE_WIDTH_IN  = 15
IMAGES_DIR     = Path("images")
EARTH_R_M      = 6_378_137.0

# (section_label, [(label, ChartStyle field, default), ...])
LAYER_GROUPS = [
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
LAYER_FIELDS = [(label, fld, default)
                for _, items in LAYER_GROUPS for (label, fld, default) in items]


# ── Page config (must be first Streamlit call) ─────────────────────────────

st.set_page_config(page_title="AOI", layout="wide", page_icon="🗺",
                   initial_sidebar_state="expanded")


# ── UI-side dataclasses (mutable; converted to engine drawables on render) ─

@dataclass
class _Placement:
    """A placed sensor / array in UI state."""
    kind: str                          # ActiveSonar / PassiveNode / PassiveArray / SurveySwath
    preset_name: str
    label: str
    color: str
    lat: float = 0.0
    lon: float = 0.0
    range_m: float = 1000
    bearing_deg: float = 0
    beam_width_deg: float = 360
    nodes: List[tuple] = field(default_factory=list)   # for PassiveArray
    track: List[tuple] = field(default_factory=list)   # for SurveySwath
    swath_m: float = 400


@dataclass
class _Measurement:
    kind: str                          # DistanceLine / AreaPolygon
    vertices: List[tuple]


# ── State init ─────────────────────────────────────────────────────────────

def _init_state():
    ss = st.session_state
    ss.setdefault("placements",     [])
    ss.setdefault("measurements",   [])
    ss.setdefault("session",        None)
    ss.setdefault("preview_b64",    None)
    ss.setdefault("preview_bounds", None)
    ss.setdefault("preview_png",    None)
    ss.setdefault("final_png",      None)
    ss.setdefault("final_filename", None)
    ss.setdefault("map_center",     DEFAULT_CENTER)
    ss.setdefault("map_zoom",       DEFAULT_ZOOM)
    ss.setdefault("last_click_sig", None)
    ss.setdefault("add_mode",       "Off")
    for label, fld, default in LAYER_FIELDS:
        ss.setdefault(f"layer_{fld}", default)


# ── Geometry helpers ───────────────────────────────────────────────────────

def _destination(lat, lon, bearing_deg, dist_m):
    """Great-circle destination from (lat, lon)."""
    br = math.radians(bearing_deg)
    d  = dist_m / EARTH_R_M
    lat1, lon1 = math.radians(lat), math.radians(lon)
    lat2 = math.asin(math.sin(lat1) * math.cos(d)
                     + math.cos(lat1) * math.sin(d) * math.cos(br))
    lon2 = lon1 + math.atan2(math.sin(br) * math.sin(d) * math.cos(lat1),
                              math.cos(d) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)


def _haversine_m(p1, p2) -> float:
    lat1, lon1 = p1
    lat2, lon2 = p2
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def _polygon_area_m2(verts) -> float:
    """Area of a lat/lon polygon in m² via local TMerc + shoelace."""
    if len(verts) < 3:
        return 0.0
    from pyproj import Transformer
    avg_lat = sum(v[0] for v in verts) / len(verts)
    avg_lon = sum(v[1] for v in verts) / len(verts)
    crs = (f"+proj=tmerc +lat_0={avg_lat} +lon_0={avg_lon} +k=1 "
           f"+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")
    t = Transformer.from_crs(4326, crs, always_xy=True)
    xys = [t.transform(lon, lat) for lat, lon in verts]
    n = len(xys)
    s = 0.0
    for i in range(n):
        j = (i + 1) % n
        s += xys[i][0] * xys[j][1] - xys[j][0] * xys[i][1]
    return abs(s) / 2.0


# ── Conversions: UI state ↔ engine ────────────────────────────────────────

def _placement_from_preset(preset_name: str, lat: float, lon: float) -> _Placement:
    p = PRESETS[preset_name]
    out = _Placement(
        kind=p["type"], preset_name=preset_name, label=preset_name,
        color=p.get("color", "#c0392b"), lat=lat, lon=lon,
    )
    if p["type"] == "ActiveSonar":
        out.range_m        = float(p.get("range_m", 1000))
        out.beam_width_deg = float(p.get("beam_width_deg", 360))
        out.bearing_deg    = float(p.get("bearing_deg", 0))
    elif p["type"] == "PassiveNode":
        out.range_m        = float(p.get("range_m", 2000))
    elif p["type"] == "PassiveArray":
        n     = int(p.get("nodes_default", 4))
        sp_m  = float(p.get("spacing_m_default", 400))
        side  = int(math.ceil(math.sqrt(n)))
        nodes = []
        for i in range(side):
            for j in range(side):
                if len(nodes) >= n:
                    break
                dy = (i - (side - 1) / 2) * sp_m
                dx = (j - (side - 1) / 2) * sp_m
                dlat = dy / 110_540
                dlon = dx / (111_320 * math.cos(math.radians(lat)))
                nodes.append((lat + dlat, lon + dlon))
        out.nodes = nodes
    elif p["type"] == "SurveySwath":
        out.swath_m = float(p.get("swath_m_default", 400))
        out.track   = [(lat - 0.005, lon), (lat + 0.005, lon)]
    return out


def _to_engine_drawable(p: _Placement):
    if p.kind == "ActiveSonar":
        return ActiveSonar(
            center=(p.lat, p.lon), range_m=p.range_m, label=p.label,
            bearing_deg=(p.bearing_deg if p.beam_width_deg < 360 else None),
            beam_width_deg=p.beam_width_deg, color=p.color)
    if p.kind == "PassiveNode":
        return PassiveNode(center=(p.lat, p.lon), range_m=p.range_m,
                           label=p.label, color=p.color)
    if p.kind == "PassiveArray":
        return PassiveArray(nodes=list(p.nodes), label=p.label, color=p.color)
    if p.kind == "SurveySwath":
        return SurveySwath(track=list(p.track), swath_m=p.swath_m,
                           label=p.label, color=p.color)
    return None


def _to_engine_measurement(m: _Measurement):
    if m.kind == "DistanceLine":
        return DistanceLine(vertices=list(m.vertices))
    if m.kind == "AreaPolygon":
        return AreaPolygon(vertices=list(m.vertices))
    return None


def _build_style() -> ChartStyle:
    ss = st.session_state
    return ChartStyle(**{fld: ss.get(f"layer_{fld}", default)
                          for _, fld, default in LAYER_FIELDS})


# ── Frame capture ──────────────────────────────────────────────────────────

def _capture_frame(map_result, aspect_key: str) -> Optional[dict]:
    """Extract (center, half_w_km, aspect, bounds) from the st_folium result."""
    if not map_result:
        return None
    b = map_result.get("bounds")
    if not b:
        return None
    sw, ne = b.get("_southWest"), b.get("_northEast")
    if not sw or not ne:
        return None
    s, w = sw["lat"], sw["lng"]
    n, e = ne["lat"], ne["lng"]
    c_lat, c_lon = (s + n) / 2, (w + e) / 2
    width_km = abs(e - w) * 111.32 * math.cos(math.radians(c_lat))
    return {
        "center":    (c_lat, c_lon),
        "half_w_km": width_km / 2,
        "aspect":    1.0 / ASPECTS[aspect_key],
        "bounds":    ((s, w), (n, e)),
    }


# ── Engine actions ─────────────────────────────────────────────────────────

def _ensure_session(frame: dict) -> AOISession:
    """Build or update the shared AOISession; fetch if needed."""
    ss = st.session_state
    if ss.session is None:
        ss.session = AOISession(
            name="streamlit",
            center=frame["center"], half_w_km=frame["half_w_km"],
            aspect=frame["aspect"],
        )
    else:
        ss.session.update(center=frame["center"],
                           half_w_km=frame["half_w_km"],
                           aspect=frame["aspect"])
    ss.session.fetch()
    return ss.session


def _do_preview(frame: dict, style: ChartStyle):
    """Render the bathy underlay (no drawables); store PNG + base64 URI."""
    ss = st.session_state
    sess = _ensure_session(frame)
    preview_w_in = MAP_WIDTH_PX / PREVIEW_DPI
    preview_h_in = preview_w_in * frame["aspect"]
    with tempfile.TemporaryDirectory() as tmp:
        out_path = sess.render(
            drawables=[], title=None, style=style,
            figsize=(preview_w_in, preview_h_in), dpi=PREVIEW_DPI,
            output_dir=Path(tmp),
        )
        png_bytes = out_path.read_bytes()
    ss.preview_b64    = base64.b64encode(png_bytes).decode()
    ss.preview_bounds = frame["bounds"]
    ss.preview_png    = png_bytes


def _do_save(frame: dict, drawables: list, style: ChartStyle, title: Optional[str]):
    """Render full-res with drawables; save to images/ + stash bytes for download."""
    ss = st.session_state
    sess = _ensure_session(frame)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = sess.render(
        drawables=drawables, title=title or None, style=style,
        figsize=(SAVE_WIDTH_IN, SAVE_WIDTH_IN * frame["aspect"]),
        dpi=SAVE_DPI, output_dir=IMAGES_DIR,
    )
    ss.final_png      = out_path.read_bytes()
    ss.final_filename = out_path.name


# ── Map building ───────────────────────────────────────────────────────────

def _build_map() -> folium.Map:
    ss = st.session_state
    m = folium.Map(location=ss.map_center, zoom_start=ss.map_zoom,
                   control_scale=True, tiles="OpenStreetMap")

    # Bathy underlay (after Preview)
    if ss.preview_b64 and ss.preview_bounds:
        folium.raster_layers.ImageOverlay(
            image=f"data:image/png;base64,{ss.preview_b64}",
            bounds=[list(b) for b in ss.preview_bounds],
            opacity=1.0,
        ).add_to(m)

    # Sensor / array / swath markers
    for i, p in enumerate(ss.placements, 1):
        _draw_placement(m, i, p)

    # Draw tool for measurements (polyline → distance, polygon → area)
    Draw(
        export=False,
        draw_options={
            "polyline":     {"shapeOptions": {"color": "#34495e", "weight": 2}},
            "polygon":      {"shapeOptions": {"color": "#34495e", "weight": 2,
                                              "fillOpacity": 0.12}},
            "marker": False, "circle": False,
            "circlemarker": False, "rectangle": False,
        },
        edit_options={"edit": True, "remove": True},
    ).add_to(m)

    return m


def _draw_placement(m: folium.Map, idx: int, p: _Placement):
    color = p.color
    label = f"S{idx}  {p.label}"
    if p.kind == "ActiveSonar":
        folium.CircleMarker(location=(p.lat, p.lon), radius=6,
                            color=color, fill=True, fill_color=color,
                            fill_opacity=0.9, tooltip=label).add_to(m)
        if p.beam_width_deg >= 360:
            folium.Circle(location=(p.lat, p.lon), radius=p.range_m,
                          color=color, weight=2, dash_array="6,3",
                          fill=True, fill_color=color, fill_opacity=0.12).add_to(m)
        else:
            half  = p.beam_width_deg / 2
            verts = [(p.lat, p.lon)]
            for k in range(33):
                theta = p.bearing_deg - half + (p.beam_width_deg * k / 32)
                verts.append(_destination(p.lat, p.lon, theta, p.range_m))
            verts.append((p.lat, p.lon))
            folium.Polygon(locations=verts, color=color, weight=2,
                           dash_array="6,3", fill=True, fill_color=color,
                           fill_opacity=0.12).add_to(m)
    elif p.kind == "PassiveNode":
        folium.CircleMarker(location=(p.lat, p.lon), radius=6,
                            color=color, fill=True, fill_color=color,
                            fill_opacity=0.9, tooltip=label).add_to(m)
        folium.Circle(location=(p.lat, p.lon), radius=p.range_m,
                      color=color, weight=2, dash_array="6,3",
                      fill=True, fill_color=color, fill_opacity=0.12).add_to(m)
    elif p.kind == "PassiveArray":
        for j, (la, lo) in enumerate(p.nodes):
            folium.CircleMarker(location=(la, lo), radius=5,
                                color=color, fill=True, fill_color=color,
                                fill_opacity=0.9,
                                tooltip=f"{label} #{j+1}").add_to(m)
    elif p.kind == "SurveySwath":
        if len(p.track) >= 2:
            folium.PolyLine(locations=list(p.track), color=color, weight=2,
                            tooltip=label).add_to(m)


# ── Sidebar UI ─────────────────────────────────────────────────────────────

def _sidebar_ui() -> Tuple[str, str, ChartStyle, str, str]:
    """Render the sidebar; return (title, aspect_key, style, preset_name, add_mode)."""
    ss = st.session_state
    sb = st.sidebar

    sb.markdown("### Title")
    title = sb.text_input("title", value="", placeholder="Leave blank for none",
                           label_visibility="collapsed", key="title_input")

    sb.markdown("### Frame")
    aspect_key = sb.selectbox(
        "aspect", options=list(ASPECTS),
        index=list(ASPECTS).index(DEFAULT_ASPECT),
        label_visibility="collapsed", key="aspect_select",
    )

    sb.markdown("### Layers")
    for section_name, items in LAYER_GROUPS:
        with sb.expander(section_name, expanded=False):
            for label, fld, default in items:
                key = f"layer_{fld}"
                st.checkbox(label, value=ss.get(key, default), key=key)
    style = _build_style()

    sb.markdown("### Sensors")
    preset_name = sb.selectbox("preset", options=list(PRESETS),
                                label_visibility="collapsed", key="preset_select")
    add_mode = sb.radio("Add at next map click", options=["Off", "On"],
                         index=0 if ss.add_mode == "Off" else 1,
                         horizontal=True, key="add_mode_radio")
    ss.add_mode = add_mode

    if not ss.placements:
        sb.caption("_No sensors yet — set Add to **On** and click on the map._")
    for i, p in enumerate(ss.placements, 1):
        with sb.expander(f"S{i}  {p.label}", expanded=False):
            _placement_form(i, p)

    sb.markdown("### Measurements")
    if not ss.measurements:
        sb.caption("_Use the polyline ↔ or polygon ▱ tools on the map._")
    for i, m in enumerate(ss.measurements, 1):
        if m.kind == "DistanceLine":
            total = sum(_haversine_m(m.vertices[j], m.vertices[j+1])
                        for j in range(len(m.vertices)-1))
            badge, val = "↔", _format_distance(total)
        else:
            badge, val = "▱", _format_area(_polygon_area_m2(m.vertices))
        sb.markdown(f"**{badge} M{i}**  {val}  · {len(m.vertices)} pt(s)")

    sb.markdown("---")
    return title, aspect_key, style, preset_name, add_mode


def _placement_form(idx: int, p: _Placement):
    """Editable fields for one placement, in a sidebar expander."""
    p.label = st.text_input("Label", value=p.label, key=f"label_{idx}")
    c1, c2 = st.columns(2)
    p.lat = c1.number_input("Lat", value=float(p.lat), format="%.5f",
                             key=f"lat_{idx}")
    p.lon = c2.number_input("Lon", value=float(p.lon), format="%.5f",
                             key=f"lon_{idx}")
    if p.kind == "ActiveSonar":
        c1, c2 = st.columns(2)
        p.range_m = c1.number_input("Range (m)", value=float(p.range_m),
                                      min_value=50.0, step=50.0,
                                      key=f"range_{idx}")
        p.beam_width_deg = c2.number_input(
            "Beam (°)", value=float(p.beam_width_deg),
            min_value=10.0, max_value=360.0, step=5.0, key=f"beam_{idx}")
        if p.beam_width_deg < 360:
            p.bearing_deg = st.number_input(
                "Bearing (°)", value=float(p.bearing_deg),
                min_value=0.0, max_value=359.0, step=5.0, key=f"bearing_{idx}")
    elif p.kind == "PassiveNode":
        p.range_m = st.number_input("Range (m)", value=float(p.range_m),
                                      min_value=50.0, step=100.0,
                                      key=f"range_pn_{idx}")
    elif p.kind == "PassiveArray":
        st.caption(f"{len(p.nodes)} nodes (positions fixed at placement time)")
    elif p.kind == "SurveySwath":
        p.swath_m = st.number_input("Swath (m)", value=float(p.swath_m),
                                      min_value=10.0, step=10.0,
                                      key=f"swath_{idx}")
    if st.button("Remove", key=f"remove_{idx}"):
        st.session_state.placements.pop(idx - 1)
        st.rerun()


# ── Map-event sync ─────────────────────────────────────────────────────────

def _sync_measurements_from_map(map_result) -> bool:
    """Replace ss.measurements with what the user has drawn on the map.
    Returns True iff anything actually changed (caller may want st.rerun)."""
    ss = st.session_state
    if not map_result or map_result.get("all_drawings") is None:
        return False
    new: List[_Measurement] = []
    for feat in map_result["all_drawings"]:
        geom = feat.get("geometry", {})
        t = geom.get("type")
        if t == "LineString":
            verts = [(la, lo) for lo, la in geom["coordinates"]]
            if len(verts) >= 2:
                new.append(_Measurement("DistanceLine", verts))
        elif t == "Polygon":
            ring = geom["coordinates"][0]
            verts = [(la, lo) for lo, la in ring[:-1]]
            if len(verts) >= 3:
                new.append(_Measurement("AreaPolygon", verts))
    sig_new = [(m.kind, tuple(m.vertices)) for m in new]
    sig_old = [(m.kind, tuple(m.vertices)) for m in ss.measurements]
    if sig_new != sig_old:
        ss.measurements = new
        return True
    return False


def _add_sensor_at_click_if_armed(map_result, preset_name: str, add_mode: str):
    """If 'add mode' is On and the user has clicked, drop a sensor there.
    Auto-disarms after one placement so an accidental second click doesn't
    spam the chart."""
    ss = st.session_state
    if add_mode != "On" or not map_result:
        return
    click = map_result.get("last_clicked")
    if not click:
        return
    sig = (click["lat"], click["lng"])
    if ss.last_click_sig == sig:
        return
    ss.last_click_sig = sig
    ss.placements.append(
        _placement_from_preset(preset_name, click["lat"], click["lng"]))
    ss.add_mode_radio = "Off"
    ss.add_mode = "Off"
    st.rerun()


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    _init_state()
    ss = st.session_state

    title, aspect_key, style, preset_name, add_mode = _sidebar_ui()

    # Map area
    ratio_wh = ASPECTS[aspect_key]
    map_height = int(MAP_WIDTH_PX / ratio_wh)
    m = _build_map()
    map_result = st_folium(
        m, width=MAP_WIDTH_PX, height=map_height, key="aoi_map",
        returned_objects=["bounds", "center", "zoom",
                          "last_clicked", "all_drawings"],
    )

    # Persist center / zoom across reruns so panning doesn't snap back
    if map_result:
        c = map_result.get("center")
        if c:
            ss.map_center = (c["lat"], c["lng"])
        z = map_result.get("zoom")
        if z is not None:
            ss.map_zoom = z

    # Sync user-drawn measurements; add sensor on click if armed
    if _sync_measurements_from_map(map_result):
        st.rerun()
    _add_sensor_at_click_if_armed(map_result, preset_name, add_mode)

    # Actions row
    cols = st.columns([1, 1, 1, 4])
    if cols[0].button("Preview bathy", type="primary", use_container_width=True):
        frame = _capture_frame(map_result, aspect_key)
        if frame:
            with st.spinner("Fetching + rendering preview …"):
                _do_preview(frame, style)
            st.rerun()
        else:
            st.warning("Map bounds not ready — pan or zoom once first.")

    if cols[1].button("Save PNG", type="primary", use_container_width=True):
        frame = _capture_frame(map_result, aspect_key)
        if frame:
            drawables = [d for d in (
                [_to_engine_drawable(p)    for p in ss.placements] +
                [_to_engine_measurement(m_) for m_ in ss.measurements]
            ) if d is not None]
            with st.spinner(
                    f"Saving — {len(ss.placements)} sensor(s), "
                    f"{len(ss.measurements)} measurement(s) …"):
                _do_save(frame, drawables, style, title)
            st.rerun()
        else:
            st.warning("Map bounds not ready — pan or zoom once first.")

    if ss.final_png:
        cols[2].download_button(
            "⬇ Download", data=ss.final_png,
            file_name=ss.final_filename or "chart.png",
            mime="image/png", use_container_width=True,
        )

    # Preview image below the map (matches the on-map overlay; easier to
    # inspect full-frame).
    if ss.preview_png:
        st.image(ss.preview_png,
                 caption=f"Preview underlay  ·  matches the map overlay  ·  "
                         f"DPI {PREVIEW_DPI}")


if __name__ == "__main__":
    main()
