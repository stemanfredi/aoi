"""Coastal bathymetric chart renderer — one publication-grade look.

Two entry points:
- `coastal_chart(name, center, half_w_km, drawables=[])` — single-shot
  convenience wrapper for ad-hoc scripts.
- `AOISession(...).render(drawables=[])` — for repeated renders against
  the same frame; reuses cached fetch + reprojection.

Full-bleed nautical line-art: black OSM coastline (from OSMData land
polygons), IHO isobaths with depth labels (sea), SRTM contours with
elevation labels (land), buff land fill, chequered scale bar, halo'd
labels and north arrow, deg-min graticule.

Land/sea boundary is the OSMData land geometry (Christoph Hormann's
pre-polygonized OSM coastline). Bathy is masked to sea-only, terrain
to land-only; single-point sensor coverage casts visibility polygons
that stop at the first land obstacle.

Default projection: local Transverse Mercator centred on the AOI —
geographic north is vertical at the chart centre. Override with
`epsg=` for a fixed UTM zone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence, Tuple

import geopandas as gpd
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrow
from matplotlib.ticker import NullLocator

from fetch import SOURCE_CITATION
from session import AOICache, AOISession


NM = 1852  # metres per nautical mile

# IHO-style isobath defaults (metres). Minor every line; major labelled.
ISOBATHS_MINOR = [2, 4, 6, 8, 10, 15, 20, 25, 30, 40, 50, 75,
                  100, 150, 200, 300, 500, 750, 1000, 1500, 2000,
                  3000, 4000, 5000]
ISOBATHS_MAJOR = [5, 10, 20, 50, 100, 200, 500, 1000, 2000]
ISOBATH_LABELS = [10, 20, 50, 100, 200, 500]

# Land terrain (positive elevation, metres above MSL) — mirror of the sea isobaths.
TERRAIN_MINOR  = [10, 25, 50, 75, 100, 150, 200, 300, 500, 750,
                  1000, 1250, 1500, 1750, 2000, 2250, 2500, 2750,
                  3000, 3500, 4000, 4500, 5000, 6000, 7000, 8000]
TERRAIN_MAJOR  = [25, 50, 100, 200, 500, 1000, 1500, 2000, 2500,
                  3000, 4000, 5000, 6000, 7000, 8000]
TERRAIN_LABELS = [50, 100, 250, 500, 1000, 1500, 2000, 3000, 4000, 5000]

LAND_BUFF       = "#e8dba8"
ISOBATH_MIN     = "#999"
ISOBATH_MAJ     = "#555"
TERRAIN_MIN     = "#a89070"
TERRAIN_MAJ     = "#5a4030"


# ── ChartStyle ──────────────────────────────────────────────────────────────

@dataclass
class ChartStyle:
    """Cosmetic + visibility controls."""
    # Colours
    land_color:        str   = LAND_BUFF
    coastline_color:   str   = "#000"
    coastline_lw:      float = 0.6
    isobath_color_min: str   = ISOBATH_MIN
    isobath_color_maj: str   = ISOBATH_MAJ
    isobath_lw_min:    float = 0.45
    isobath_lw_maj:    float = 0.55
    terrain_color_min: str   = TERRAIN_MIN
    terrain_color_maj: str   = TERRAIN_MAJ
    terrain_lw_min:    float = 0.45
    terrain_lw_maj:    float = 0.50

    # Visibility
    show_land_fill:        bool = True
    show_isobaths:         bool = True
    show_isobath_labels:   bool = True
    show_terrain_contours: bool = True
    show_terrain_labels:   bool = True
    show_coastline:        bool = True
    show_port_features:    bool = True
    show_place_names:      bool = True
    show_sensors:          bool = True
    show_graticule:        bool = True
    show_tick_labels:      bool = True
    show_scale_bar:        bool = True
    show_scale_ratio:      bool = False   # "1 : N" text above the bar; off by default
    show_north_arrow:      bool = True
    show_caption:          bool = True
    show_title:            bool = True

    # Behaviour
    emphasize_major_contours: bool  = True   # False → all contours at minor lw
    show_sensor_legend:       bool  = True   # True → inline S1/S2/… + legend block
                                              #         mapping indices to full descriptions
    caption_extra_lines:      tuple = ()


# ── Land/sea masking against the OSMData land geometry ─────────────────────

def _mask_to(arr, extent, land_geom, *, keep):
    """Mask a raster against an OSMData land geometry.

    `keep='sea'`  → set NaN inside `land_geom` (bathy stays sea-only).
    `keep='land'` → set NaN outside `land_geom` (terrain stays land-only).
    Returns the array unchanged when `land_geom` is None or empty.
    """
    if arr is None or land_geom is None or land_geom.is_empty:
        return arr
    from affine import Affine
    from rasterio.features import geometry_mask
    ny, nx = arr.shape
    x0, x1, y0, y1 = extent
    transform = (Affine.translation(x0, y1)
                 * Affine.scale((x1 - x0) / nx, -(y1 - y0) / ny))
    # geometry_mask returns True OUTSIDE the geometry; invert for "is land".
    is_land = ~geometry_mask([land_geom.__geo_interface__],
                              out_shape=(ny, nx), transform=transform,
                              all_touched=False)
    out = arr.astype(float, copy=True)
    out[is_land if keep == "sea" else ~is_land] = np.nan
    return out


# ── Graticule, north arrow, scale bar ───────────────────────────────────────

def _format_dm(deg, axis):
    """Decimal degrees → degrees-minutes string. axis ∈ {'lat', 'lon'}."""
    a = abs(deg)
    d = int(a)
    m = (a - d) * 60
    suffix = ("N" if deg >= 0 else "S") if axis == "lat" else ("E" if deg >= 0 else "W")
    return f"{d:d}°{m:04.1f}'{suffix}" if d else f"0°{m:04.1f}'{suffix}"


def _nice_step(span_deg, n_target):
    """Pick a 'nice' graticule interval in degrees for ~n_target ticks."""
    raw = span_deg / max(n_target, 1)
    for s in (1.0, 0.5, 1/3, 0.25, 1/6, 1/12, 2/60, 1/60, 0.5/60, 0.25/60):
        if raw > s * 0.85:
            return s
    return raw


def _graticule(ax, inv, fwd, xmin, xmax, ymin, ymax, *,
                n_ticks=4, show_tick_labels=True,
                title_exclude_bbox=None):
    """Lat/lon graticule with halo'd tick labels inside the frame.

    `title_exclude_bbox` (xlo, ylo, xhi, yhi) suppresses any tick label
    whose anchor point falls inside the box — used to prevent collisions
    with the in-frame title text.
    """
    cx = np.array([xmin, xmax, xmin, xmax, (xmin + xmax) / 2])
    cy = np.array([ymin, ymin, ymax, ymax, (ymin + ymax) / 2])
    lons, lats = inv.transform(cx, cy)
    lon_min, lon_max = float(lons.min()), float(lons.max())
    lat_min, lat_max = float(lats.min()), float(lats.max())

    lon_step = _nice_step(lon_max - lon_min, n_ticks)
    lat_step = _nice_step(lat_max - lat_min, n_ticks)

    xtick_pos, xtick_lab = [], []
    for lon in np.arange(np.ceil(lon_min / lon_step) * lon_step, lon_max + 1e-9, lon_step):
        lat_dense = np.linspace(lat_min, lat_max, 64)
        xs, ys = fwd.transform(np.full_like(lat_dense, lon), lat_dense)
        ax.plot(xs, ys, color="#888", lw=0.4, alpha=0.45, zorder=8)
        x_at_bottom, _ = fwd.transform(lon, lat_min)
        if xmin <= x_at_bottom <= xmax:
            xtick_pos.append(x_at_bottom)
            xtick_lab.append(_format_dm(lon, "lon"))

    ytick_pos, ytick_lab = [], []
    for lat in np.arange(np.ceil(lat_min / lat_step) * lat_step, lat_max + 1e-9, lat_step):
        lon_dense = np.linspace(lon_min, lon_max, 64)
        xs, ys = fwd.transform(lon_dense, np.full_like(lon_dense, lat))
        ax.plot(xs, ys, color="#888", lw=0.4, alpha=0.45, zorder=8)
        _, y_at_left = fwd.transform(lon_min, lat)
        if ymin <= y_at_left <= ymax:
            ytick_pos.append(y_at_left)
            ytick_lab.append(_format_dm(lat, "lat"))

    ax.set_xticks([]); ax.set_yticks([])
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    if not show_tick_labels:
        return
    halo = [pe.withStroke(linewidth=2.4, foreground="white")]
    edge_dx = (xmax - xmin) * 0.004
    edge_dy = (ymax - ymin) * 0.004

    def _in_excl(x, y):
        if title_exclude_bbox is None:
            return False
        xlo, ylo, xhi, yhi = title_exclude_bbox
        return xlo <= x <= xhi and ylo <= y <= yhi

    for x, lab in zip(xtick_pos, xtick_lab):
        if _in_excl(x, ymin + edge_dy):
            continue
        ax.text(x, ymin + edge_dy, lab, ha="center", va="bottom",
                fontsize=8, color="#111", zorder=11, path_effects=halo)
    for y, lab in zip(ytick_pos, ytick_lab):
        if _in_excl(xmin + edge_dx, y):
            continue
        ax.text(xmin + edge_dx, y, lab, ha="left", va="center",
                fontsize=8, color="#111", zorder=11, path_effects=halo)


def _scale_bar_geometry(xmin, xmax, ymin, ymax):
    """Pick a nice round-number bar length + position for the scale bar.

    Shared by the bar and the ratio label so both anchor identically and
    appear together when both are enabled (or each independently).
    """
    span = xmax - xmin
    target = span * 0.12
    bar_km = min([0.5, 1, 2, 5, 10, 20, 50, 100],
                 key=lambda v: abs(v * 1000 - target))
    bar_m = bar_km * 1000
    bx_right = xmax - span * 0.04
    bx_left  = bx_right - bar_m
    by = ymin + (ymax - ymin) * 0.04
    bar_h = (ymax - ymin) * 0.006
    return bar_km, bar_m, bx_left, bx_right, by, bar_h


def _scale_bar(ax, xmin, xmax, ymin, ymax):
    """Chequered scale bar at the bottom-right with km + nm labels."""
    bar_km, bar_m, bx_left, bx_right, by, bar_h = _scale_bar_geometry(
        xmin, xmax, ymin, ymax)
    bar_nm = bar_m / NM
    span = xmax - xmin
    halo = [pe.withStroke(linewidth=2.0, foreground="white")]

    n_seg = 5
    seg_w = bar_m / n_seg
    for i in range(n_seg):
        ax.add_patch(plt.Rectangle((bx_left + i * seg_w, by), seg_w, bar_h,
                                    facecolor="#111" if i % 2 == 0 else "#fff",
                                    edgecolor="#111", linewidth=0.8, zorder=10))
    for i in range(n_seg + 1):
        val = (bar_km / n_seg) * i
        ax.text(bx_left + i * seg_w, by + bar_h + (ymax - ymin) * 0.004,
                f"{val:g}", ha="center", va="bottom",
                fontsize=7, color="#111", zorder=11, path_effects=halo)
    ax.text(bx_right + span * 0.006, by + bar_h / 2, "km",
            ha="left", va="center", fontsize=7, fontweight="bold",
            color="#111", zorder=11, path_effects=halo)
    ax.text(bx_left + bar_m / 2, by - (ymax - ymin) * 0.006,
            f"≈ {bar_nm:.2f} nm", ha="center", va="top",
            fontsize=6.5, color="#333", zorder=11, path_effects=halo)


def _scale_ratio_label(ax, xmin, xmax, ymin, ymax, ratio):
    """Render '1 : N' above the scale-bar position. Independent of the bar."""
    if not ratio:
        return
    _, bar_m, bx_left, _, by, bar_h = _scale_bar_geometry(xmin, xmax, ymin, ymax)
    ax.text(bx_left + bar_m / 2,
            by + bar_h + (ymax - ymin) * 0.022,
            f"1 : {ratio:,.0f}",
            ha="center", va="bottom", fontsize=9.5, fontweight="bold",
            color="#111", zorder=11,
            path_effects=[pe.withStroke(linewidth=2.0, foreground="white")])


def _north_arrow(ax, xmin, xmax, ymin, ymax):
    """Halo'd filled triangle + 'N', top-right."""
    span_x, span_y = xmax - xmin, ymax - ymin
    cx = xmax - span_x * 0.05
    cy = ymax - span_y * 0.10
    arrow_len = span_y * 0.06
    halo = [pe.withStroke(linewidth=2.8, foreground="white")]
    arrow = FancyArrow(cx, cy, 0, arrow_len,
                       width=arrow_len * 0.08,
                       head_width=arrow_len * 0.35,
                       head_length=arrow_len * 0.35,
                       fc="#111", ec="#111", zorder=10,
                       length_includes_head=True)
    arrow.set_path_effects(halo)
    ax.add_patch(arrow)
    txt = ax.text(cx, cy + arrow_len + span_y * 0.005, "N",
                  ha="center", va="bottom", fontsize=10,
                  fontweight="bold", color="#111", zorder=10)
    txt.set_path_effects(halo)


def _caption(ax, lines, xmin, ymin, xmax, ymax):
    """Halo'd caption inside the bottom-left of the chart."""
    ax.text(xmin + (xmax - xmin) * 0.004, ymin + (ymax - ymin) * 0.020,
            "  ·  ".join(lines),
            ha="left", va="bottom", fontsize=6.5, color="#333", zorder=11,
            path_effects=[pe.withStroke(linewidth=2.0, foreground="white")])


def _sensor_legend(ax, sensors, xmin, ymin, xmax, ymax):
    """Draw a sensor-index legend block at the bottom-left, above the caption.

    Each row: 'Sn  <legend_text>'. Skips primitives whose .legend_text()
    returns None (TextLabel, ZonePolygon). When no sensor has a non-None
    legend_text, the block is omitted entirely.
    """
    rows = []
    for i, s in enumerate(sensors, 1):
        get = getattr(s, "legend_text", None)
        text = get() if callable(get) else None
        if not text:
            continue
        color = getattr(s, "color", None) or "#222"
        rows.append((f"S{i}", text, color))
    if not rows:
        return

    halo = [pe.withStroke(linewidth=2.4, foreground="white")]
    line_h = (ymax - ymin) * 0.022
    x = xmin + (xmax - xmin) * 0.004
    # Top-down: S1 at top, last sensor just above the caption line.
    y0 = ymin + (ymax - ymin) * 0.038
    n = len(rows)
    for k, (tag, text, color) in enumerate(rows):
        y = y0 + (n - 1 - k) * line_h
        ax.text(x, y, tag, ha="left", va="bottom",
                fontsize=8, fontweight="bold", color=color,
                zorder=11, path_effects=halo)
        ax.text(x + (xmax - xmin) * 0.018, y, text,
                ha="left", va="bottom", fontsize=7.5, color="#222",
                zorder=11, path_effects=halo)


def _scale_ratio(half_w_m, figsize_inches_w):
    """Representative fraction 1 : N for the chart at full-bleed paper width."""
    paper_data_m = figsize_inches_w * 0.0254
    return (2 * half_w_m) / paper_data_m if paper_data_m > 0 else None


# ── Place names ─────────────────────────────────────────────────────────────

def _draw_places(ax, places_xy, xmin, ymin, xmax, ymax):
    """OSM place-name markers, filtered by AOI scale to control label density.

    > 50 km half-width  → city + town only
    > 20 km             → + village
    > 10 km             → + suburb
    ≤ 10 km             → all (incl. hamlet)
    """
    if places_xy is None or places_xy.empty or "name" not in places_xy.columns:
        return

    half_w_km = (xmax - xmin) / 2 / 1000
    if   half_w_km > 50: allowed = {"city", "town"}
    elif half_w_km > 20: allowed = {"city", "town", "village"}
    elif half_w_km > 10: allowed = {"city", "town", "village", "suburb"}
    else:                allowed = {"city", "town", "village", "suburb", "hamlet"}

    size_by_place = {
        "city":    (4.0, 9.5, "bold"),
        "town":    (3.5, 8.5, "bold"),
        "village": (2.5, 7.5, "normal"),
        "suburb":  (2.5, 7.5, "normal"),
        "hamlet":  (2.0, 7.0, "normal"),
    }
    halo = [pe.withStroke(linewidth=2.5, foreground="white")]
    for _, row in places_xy.iterrows():
        name = row.get("name")
        if not isinstance(name, str) or not name:
            continue
        place = row.get("place", "town") if "place" in places_xy.columns else "town"
        if place not in allowed:
            continue
        marker_sz, font_sz, weight = size_by_place.get(place, (2.5, 7.5, "normal"))
        x, y = row.geometry.x, row.geometry.y
        if not (xmin <= x <= xmax and ymin <= y <= ymax):
            continue
        ax.plot(x, y, "o", color="#222", markersize=marker_sz, zorder=6.0,
                markeredgecolor="white", markeredgewidth=0.6)
        ax.text(x + (xmax - xmin) * 0.005, y, name,
                fontsize=font_sz, color="#111", fontweight=weight,
                ha="left", va="center", zorder=6.1, path_effects=halo)


# ── Sensors ─────────────────────────────────────────────────────────────────

def _draw_sensors(ax, sensors, fwd, xmin, ymin, xmax, ymax, *,
                   compact_labels, sea_clip=None, land_obstacles=None):
    """Render every sensor / annotation primitive.

    `sea_clip` (a shapely geometry of the sea portion of the chart frame)
    clips water-relative coverage (Voronoi cells, swath buffers) at the
    coastline. `land_obstacles` (the land geometry) enables visibility-
    polygon shadow-casting for single-point active and passive sensors —
    rays stop at the first land obstacle so coverage doesn't appear
    behind islands or breakwaters (METHODOLOGY §8: line-of-sight is the
    dominant constraint in shallow water).
    """
    to_chart = lambda lon, lat: fwd.transform(lon, lat)
    for idx, s in enumerate(sensors, start=1):
        override = f"S{idx}" if compact_labels else None
        s.draw(ax, to_chart, clip_bbox=(xmin, ymin, xmax, ymax),
                sea_clip=sea_clip, land_obstacles=land_obstacles,
                label_override=override)


# ── Output path ─────────────────────────────────────────────────────────────

def _output_path(base_dir, center):
    """`<base>/<lat>{N|S}_<lon>{E|W}_<YYYYMMDDTHHMMSS>.png`."""
    lat, lon = center
    lat_s = f"{abs(lat):.3f}{'N' if lat >= 0 else 'S'}"
    lon_s = f"{abs(lon):.3f}{'E' if lon >= 0 else 'W'}"
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return base_dir / f"{lat_s}_{lon_s}_{ts}.png"


# ── Main renderer ───────────────────────────────────────────────────────────

def coastal_chart(
    name: str,
    center: Tuple[float, float],
    half_w_km: float,
    *,
    aspect: float = 2 / 3,
    drawables: Sequence = (),
    isobath_minor: Optional[Sequence[float]] = None,
    isobath_major: Optional[Sequence[float]] = None,
    label_levels: Sequence[float] = ISOBATH_LABELS,
    title: Optional[str] = None,
    source: str = "auto",
    epsg: Optional[int] = None,
    pad_deg: float = 0.05,
    figsize: Tuple[float, float] = (15, 10),
    dpi: int = 220,
    style: Optional[ChartStyle] = None,
    output_dir: Optional[Path] = None,
) -> Path:
    """Single-shot render: build session, fetch, render, return path.

    Use AOISession directly when the same frame will render more than
    once (Preview + Save in the designer, for example) — repeated calls
    against a session reuse the cache and cost only the render step.

    Filenames are `<lat>N_<lon>E_<YYYYMMDDTHHMMSS>.png` — unique by
    construction.
    """
    style = style or ChartStyle()
    session = AOISession(
        name=name, center=center, half_w_km=half_w_km,
        aspect=aspect, epsg=epsg,
        source=source, pad_deg=pad_deg,
        need_terrain=style.show_terrain_contours,
        need_port=style.show_port_features,
        need_places=style.show_place_names,
    )
    return session.render(
        drawables=drawables, title=title, style=style,
        isobath_minor=isobath_minor, isobath_major=isobath_major,
        label_levels=label_levels,
        figsize=figsize, dpi=dpi, output_dir=output_dir,
    )


def render_from_cache(
    cache: AOICache,
    *,
    name: str,
    center: Tuple[float, float],
    drawables: Sequence = (),
    title: Optional[str] = None,
    style: Optional[ChartStyle] = None,
    isobath_minor: Optional[Sequence[float]] = None,
    isobath_major: Optional[Sequence[float]] = None,
    label_levels: Optional[Sequence[float]] = None,
    figsize: Tuple[float, float] = (15, 10),
    dpi: int = 220,
    output_dir: Optional[Path] = None,
) -> Path:
    """Render the chart from a populated AOICache. Pure: no I/O beyond
    the final PNG write. Cheap (~0.3–0.8 s)."""
    style = style or ChartStyle()
    out_dir = Path(output_dir) if output_dir else Path.cwd() / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    outpath = _output_path(out_dir, center)
    minor = list(isobath_minor) if isobath_minor else ISOBATHS_MINOR
    major = list(isobath_major) if isobath_major else ISOBATHS_MAJOR
    levels = list(label_levels) if label_levels else list(ISOBATH_LABELS)
    _render_to(cache, drawables, minor, major, levels, title, style,
               figsize, dpi, outpath)
    print(f"  saved {outpath}")
    return outpath


def _render_to(c: AOICache, drawables, minor, major, label_levels,
                title, style, figsize, dpi, outpath):
    """The matplotlib step. Pure function of (cache, drawables, style)."""
    from shapely.geometry import box as shp_box
    from shapely.ops import unary_union

    xmin, ymin, xmax, ymax = c.bbox
    chart_width_m = xmax - xmin
    fig, ax = plt.subplots(figsize=figsize)
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    fig.patch.set_facecolor("white"); ax.set_facecolor("white")
    ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")
    ax.xaxis.set_major_locator(NullLocator())
    ax.yaxis.set_major_locator(NullLocator())
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Land + sea geometry from the cache's OSMData land polygons.
    bbox_poly = shp_box(xmin, ymin, xmax, ymax)
    if c.land_xy is not None and not c.land_xy.empty:
        land_geom = unary_union(c.land_xy.geometry.tolist()).intersection(bbox_poly)
    else:
        land_geom = None
    sea_geom = bbox_poly.difference(land_geom) if land_geom else bbox_poly

    z_sea = _mask_to(c.z, c.extent, land_geom, keep="sea")
    t_z_land = (_mask_to(c.t_z, c.t_extent, land_geom, keep="land")
                if c.t_z is not None else None)
    depth = np.where(z_sea < 0, -z_sea, np.nan)

    # Land fill: buff background + sea polygon painted white on top.
    if style.show_land_fill:
        ax.set_facecolor(style.land_color)
        if not sea_geom.is_empty:
            gpd.GeoSeries([sea_geom], crs=c.chart_crs).plot(
                ax=ax, facecolor="white", edgecolor="none", zorder=1)

    # Isobaths (sea). When emphasis is OFF, majors render identically to
    # minors (color + lw + alpha) — only the labels distinguish them.
    if style.show_isobaths:
        nx, ny = c.z.shape[1], c.z.shape[0]
        xx = np.linspace(c.extent[0], c.extent[1], nx)
        yy = np.linspace(c.extent[3], c.extent[2], ny)
        ax.contour(xx, yy, depth, levels=minor,
                   colors=style.isobath_color_min,
                   linewidths=style.isobath_lw_min, alpha=0.85, zorder=2)
        if style.emphasize_major_contours:
            maj_color, maj_lw, maj_alpha = (style.isobath_color_maj,
                                             style.isobath_lw_maj, 1.0)
        else:
            maj_color, maj_lw, maj_alpha = (style.isobath_color_min,
                                             style.isobath_lw_min, 0.85)
        cs = ax.contour(xx, yy, depth, levels=major,
                        colors=maj_color, linewidths=maj_lw,
                        alpha=maj_alpha, zorder=2.2)
        if style.show_isobath_labels:
            _label_contours(ax, cs, label_levels)

    # Terrain contours (land). Same emphasis logic as bathy.
    if t_z_land is not None and style.show_terrain_contours:
        t_ny, t_nx = t_z_land.shape
        t_xx = np.linspace(c.t_extent[0], c.t_extent[1], t_nx)
        t_yy = np.linspace(c.t_extent[3], c.t_extent[2], t_ny)
        elev = np.where(t_z_land > 0, t_z_land, np.nan)
        if np.any(np.isfinite(elev)):
            ax.contour(t_xx, t_yy, elev, levels=TERRAIN_MINOR,
                       colors=style.terrain_color_min,
                       linewidths=style.terrain_lw_min, alpha=0.75, zorder=2.05)
            if style.emphasize_major_contours:
                tmaj_color, tmaj_lw, tmaj_alpha = (style.terrain_color_maj,
                                                    style.terrain_lw_maj, 1.0)
            else:
                tmaj_color, tmaj_lw, tmaj_alpha = (style.terrain_color_min,
                                                    style.terrain_lw_min, 0.75)
            cs_t = ax.contour(t_xx, t_yy, elev, levels=TERRAIN_MAJOR,
                              colors=tmaj_color, linewidths=tmaj_lw,
                              alpha=tmaj_alpha, zorder=2.25)
            if style.show_terrain_labels:
                _label_contours(ax, cs_t, TERRAIN_LABELS)

    # Inland water (lakes / reservoirs / lagoons) — white on buff land.
    if c.inland_xy is not None and not c.inland_xy.empty:
        c.inland_xy.plot(ax=ax, facecolor="white", edgecolor="none", zorder=2.6)

    # Coastline = boundary of OSMData land geometry (always closed).
    if style.show_coastline:
        shore_kw = dict(color=style.coastline_color,
                        linewidth=style.coastline_lw, zorder=3)
        if land_geom is not None and not land_geom.is_empty:
            gpd.GeoSeries([land_geom.boundary], crs=c.chart_crs).plot(
                ax=ax, **shore_kw)
        if c.inland_xy is not None and not c.inland_xy.empty:
            c.inland_xy.boundary.plot(ax=ax, **shore_kw)

    # Port features (outline-only).
    if style.show_port_features:
        if c.piers_xy is not None:
            pp = c.piers_xy[c.piers_xy.geom_type.isin(["Polygon", "MultiPolygon"])]
            pl = c.piers_xy[c.piers_xy.geom_type.isin(["LineString", "MultiLineString"])]
            if not pp.empty: pp.boundary.plot(ax=ax, color="#000", linewidth=0.8, zorder=4)
            if not pl.empty: pl.plot(ax=ax, color="#000", linewidth=0.8, zorder=4)
        if c.streets_xy is not None:
            c.streets_xy.plot(ax=ax, color="#555", linewidth=0.3, alpha=0.55, zorder=3.7)
        if c.bldgs_xy is not None:
            bp = c.bldgs_xy[c.bldgs_xy.geom_type.isin(["Polygon", "MultiPolygon"])]
            if not bp.empty:
                bp.boundary.plot(ax=ax, color="#333", linewidth=0.3,
                                  alpha=0.6, zorder=4)

    if style.show_place_names:
        _draw_places(ax, c.places_xy, xmin, ymin, xmax, ymax)

    # Single-point sensors (ActiveSonar, PassiveNode) cast a visibility
    # polygon against `land_geom` — coverage stops at the first land
    # obstacle. SurveySwath + PassiveArray fall back to `sea_clip`.
    if style.show_sensors:
        _draw_sensors(ax, drawables, c.fwd, xmin, ymin, xmax, ymax,
                       compact_labels=style.show_sensor_legend,
                       sea_clip=sea_geom, land_obstacles=land_geom)

    # Title text first → its bbox excludes nearby tick labels in the
    # graticule below.
    title_excl = None
    if style.show_title and title:
        tx = xmin + (xmax - xmin) * 0.012
        ty = ymax - (ymax - ymin) * 0.018
        ax.text(tx, ty, title, ha="left", va="top",
                fontsize=12, fontweight="bold",
                color="#111", zorder=11,
                path_effects=[pe.withStroke(linewidth=2.6, foreground="white")])
        em_x = (xmax - xmin) * 0.013
        em_y = (ymax - ymin) * 0.030
        excl_w = max(0.18 * (xmax - xmin), em_x * len(title) * 0.62)
        title_excl = (tx - em_x * 0.4, ty - em_y * 1.2,
                      tx + excl_w,    ty + em_y * 0.4)

    if style.show_graticule:
        _graticule(ax, c.inv, c.fwd, xmin, xmax, ymin, ymax,
                   show_tick_labels=style.show_tick_labels,
                   title_exclude_bbox=title_excl)
    if style.show_north_arrow:
        _north_arrow(ax, xmin, xmax, ymin, ymax)

    if style.show_scale_bar:
        _scale_bar(ax, xmin, xmax, ymin, ymax)
    if style.show_scale_ratio:
        rf = _scale_ratio((xmax - xmin) / 2, figsize[0])
        _scale_ratio_label(ax, xmin, xmax, ymin, ymax, rf)

    if style.show_sensor_legend:
        _sensor_legend(ax, drawables, xmin, ymin, xmax, ymax)

    if style.show_caption:
        lines = [
            f"WGS84 / {c.chart_desc}",
            f"Bathymetry: {SOURCE_CITATION.get(c.src_tag, c.src_tag)}",
        ]
        if c.t_z is not None:
            lines.append("Terrain: SRTM 30 m (OpenTopography)")
        lines.append("Coastline: OSMData land polygons · Depth in metres below MSL")
        cells_across = c.z.shape[1]
        cells_per_km = cells_across / max(chart_width_m / 1000.0, 1e-6)
        lines.append(f"Native bathy: {cells_across} cells across "
                     f"(~{1000.0/cells_per_km:.0f} m/cell)")
        lines.append(f"Rendered {datetime.now().strftime('%Y-%m-%d')}")
        lines.extend(style.caption_extra_lines)
        _caption(ax, lines, xmin, ymin, xmax, ymax)

    fig.savefig(outpath, dpi=dpi, facecolor="white")
    plt.close(fig)


def _label_contours(ax, cs, label_levels):
    """Label a contour set at the requested levels with a white halo."""
    levels = [lv for lv in label_levels if lv in cs.levels]
    if not levels:
        return
    try:
        labels = ax.clabel(cs, levels=levels, inline=True, fontsize=8,
                            fmt=lambda v: f"{int(v)} m", use_clabeltext=True)
        for txt in labels:
            txt.set_path_effects([pe.withStroke(linewidth=2.0, foreground="white")])
    except Exception:
        pass
