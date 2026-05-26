"""Underwater sensor primitives for chart overlays.

Four sensor archetypes — chosen to match how real systems map onto a chart:

  ActiveSonar  — single-point active sonar (diver-detection, port-protection).
                 Coverage drawn as a range circle (omni) or wedge (sector).
                 Examples: Forcys Sentinel (~0.6-1 km divers), DSIT AquaShield
                 (~1.5-3.5 km divers/SDV).

  PassiveNode  — single hydrophone pod. Coverage drawn as a transparent
                 range circle (dashed edge — the circle is a stylized
                 single-pod sensitivity envelope against a stated target
                 class, NOT a measured footprint; see METHODOLOGY.md §6).
                 Use for sketching individual pods one at a time; place the
                 next pod at the edge of the previous circle for s=r
                 cross-bearing geometry (METHODOLOGY.md §8).

  PassiveArray — fiber-optic / cabled hydrophone array. Coverage drawn as
                 node markers + Voronoi cells (per-node "responsibility area").
                 Examples: Optics11 OptiBarrier, Image Soft IS UNWAS.

  SurveySwath  — multibeam / sub-bottom survey sonar mapped along a track.
                 Coverage drawn as a swath corridor (line buffer).
                 Examples: Norbit WINGHEAD i80S (~3-5 × depth swath, 200 kHz).

Plus two annotation primitives (not sensors, but charted alongside them):

  TextLabel    — text at a single position. For "FPSO HQ", "PROHIBITED",
                 "INCIDENT 2024-03", etc.

  ZonePolygon  — arbitrary closed polygon with optional fill + label.
                 For threat corridors, exclusion zones, no-go areas, AOI
                 sub-regions. Linestyle: solid / dashed / dotted.

All inputs are in lat/lon (EPSG:4326). The .draw(ax, to_chart, *, sea_clip=…)
method takes a `to_chart` callable (lon, lat → x_chart, y_chart) so the
same object can render into any projection chart.py picks. When
`sea_clip` is provided (a shapely geometry of the sea portion of the
chart frame), water-relative coverage (range circles, wedges, swath
buffers, Voronoi cells) is clipped to it so coverage never extends onto
land.
"""
from dataclasses import dataclass
from typing import Sequence, Tuple, Callable, Optional

import numpy as np
from shapely.geometry import Point, LineString, Polygon, MultiPolygon, box
from shapely.ops import voronoi_diagram, unary_union
import geopandas as gpd

LatLon = Tuple[float, float]   # (lat, lon) — same convention as chart.coastal_chart

DEFAULT_COLORS = {
    "active":  ("#c0392b", 0.18),   # red, 18% fill
    "passive": ("#2980b9", 0.15),   # blue
    "survey":  ("#16a085", 0.20),   # teal
    "zone":    ("#7f8c8d", 0.20),   # grey
    "label":   ("#222222", 0.0),    # dark text, no fill
}

MEASURE_COLOR = "#34495e"           # dark slate — distinct from sensor palettes


def _format_distance(m: float) -> str:
    """Auto-format a metric distance as 'km · nm' (or 'm · nm' for sub-km)."""
    nm = m / 1852.0
    km = m / 1000.0
    if km < 1.0:
        return f"{m:.0f} m · {nm:.2f} nm"
    if km < 10:
        return f"{km:.2f} km · {nm:.2f} nm"
    return f"{km:.1f} km · {nm:.1f} nm"


def _format_area(m2: float) -> str:
    """Auto-format a metric area: ha when small, km² + ha mid, km² when large."""
    ha = m2 / 1e4
    km2 = m2 / 1e6
    if ha < 100:
        return f"{ha:.1f} ha"
    if km2 < 100:
        return f"{km2:.2f} km² · {ha:.0f} ha"
    return f"{km2:.0f} km²"


def _clip(geom, sea_clip):
    """Intersect a geom against sea_clip when provided. Empty → returns geom."""
    if sea_clip is None or geom is None or geom.is_empty:
        return geom
    return geom.intersection(sea_clip)


def _visibility_polygon(cx, cy, max_range, land_geom, *,
                         bearing_deg=None, beam_width_deg=360,
                         n_rays=180):
    """Star-shaped polygon of points visible from (cx, cy) within max_range,
    occluded by `land_geom`. Models acoustic line-of-sight for single-point
    sensors: each ray stops at the first land obstacle, so areas behind
    islands / breakwaters are correctly excluded from the coverage.

    For sectors (`beam_width_deg < 360`), rays are cast only within the
    angular wedge `[bearing - half, bearing + half]` and the polygon
    closes back to the sensor center (apex of the wedge).

    No diffraction, no bathy-dependent shadow — see METHODOLOGY §3:
    geometric instrument, performance prediction is BELLHOP territory.
    """
    import math as _math
    if land_geom is None or land_geom.is_empty:
        # No obstacles → full circle / wedge as before
        if beam_width_deg >= 360:
            return Point(cx, cy).buffer(max_range, resolution=64)
        return _wedge_polygon(cx, cy, max_range, bearing_deg,
                              beam_width_deg, n=max(n_rays, 64))

    if beam_width_deg >= 360:
        thetas = np.linspace(0, 2 * _math.pi, n_rays, endpoint=False)
    else:
        half = beam_width_deg / 2.0
        # Scale ray count by sector size, with a sensible floor.
        n_sector = max(int(n_rays * beam_width_deg / 360), 16)
        compass_angles = np.linspace(bearing_deg - half,
                                      bearing_deg + half, n_sector)
        thetas = np.deg2rad(90.0 - compass_angles)

    points = []
    for theta in thetas:
        ex = cx + max_range * _math.cos(theta)
        ey = cy + max_range * _math.sin(theta)
        ray = LineString([(cx, cy), (ex, ey)])
        hit = ray.intersection(land_geom)
        if hit.is_empty:
            points.append((ex, ey))
            continue
        # Collect all candidate impact points and pick the closest to viewpoint.
        coords = []
        gtype = hit.geom_type
        if gtype == "Point":
            coords.append((hit.x, hit.y))
        elif gtype == "MultiPoint":
            coords.extend((p.x, p.y) for p in hit.geoms)
        elif gtype == "LineString":
            coords.extend(hit.coords)
        elif gtype == "MultiLineString":
            for g in hit.geoms:
                coords.extend(g.coords)
        elif gtype == "GeometryCollection":
            for g in hit.geoms:
                if g.geom_type == "Point":
                    coords.append((g.x, g.y))
                elif g.geom_type in ("LineString", "MultiLineString"):
                    if g.geom_type == "LineString":
                        coords.extend(g.coords)
                    else:
                        for sub in g.geoms:
                            coords.extend(sub.coords)
        if coords:
            points.append(min(coords,
                              key=lambda p: (p[0]-cx)**2 + (p[1]-cy)**2))
        else:
            points.append((ex, ey))

    if beam_width_deg < 360:
        # Sector visibility closes back to the sensor center.
        ring = [(cx, cy)] + points + [(cx, cy)]
    else:
        ring = points + [points[0]]
    if len(ring) < 4:
        return Point(cx, cy).buffer(max_range, resolution=64)
    poly = Polygon(ring)
    # buffer(0) repairs any self-intersections from tangent rays
    return poly if poly.is_valid else poly.buffer(0)


def _polys_of(geom):
    """Iterate Polygons from a Polygon/MultiPolygon/empty geom."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return []


@dataclass
class ActiveSonar:
    """Single-point active sonar with a stated detection range.

    `range_m` is the published or model-rated detection range (e.g. 900 m
    against divers for Forcys Sentinel).

    `bearing_deg` (compass: 0=N, 90=E) and `beam_width_deg` together control
    coverage:
      - beam_width_deg = 360 (default)  → omnidirectional, drawn as a circle.
      - beam_width_deg < 360            → directional sector centred on
        bearing_deg, drawn as a wedge polygon.

    `outline=True` (default) draws a dashed edge around the coverage poly.
    Set `False` for a cleaner look — just the filled wash + centre marker.
    """
    center: LatLon                  # (lat, lon)
    range_m: float
    label: str
    bearing_deg:    Optional[float] = None
    beam_width_deg: float           = 360
    color: Optional[str]   = None
    alpha: Optional[float] = None
    outline: bool          = True

    def coverage_polygon(self, to_chart: Callable):
        """Return the unclipped coverage polygon in chart coords."""
        lat, lon = self.center
        cx, cy = to_chart(lon, lat)
        if self.beam_width_deg >= 360:
            return Point(cx, cy).buffer(self.range_m, resolution=64)
        if self.bearing_deg is None:
            raise ValueError("ActiveSonar with beam_width_deg < 360 needs bearing_deg")
        return _wedge_polygon(cx, cy, self.range_m, self.bearing_deg,
                              self.beam_width_deg, n=64)

    def draw(self, ax, to_chart: Callable, *, sea_clip=None,
              land_obstacles=None,
              label_fontsize: float = 10,
              label_override: Optional[str] = None, **_):
        c, a = DEFAULT_COLORS["active"]
        color, alpha = self.color or c, self.alpha if self.alpha is not None else a
        lat, lon = self.center
        cx, cy = to_chart(lon, lat)
        # Visibility polygon when land obstacles are known — models acoustic
        # line-of-sight (rays stop at first land). Falls back to simple
        # sea_clip intersection when no land geometry is available.
        if land_obstacles is not None:
            poly = _visibility_polygon(
                cx, cy, self.range_m, land_obstacles,
                bearing_deg=self.bearing_deg,
                beam_width_deg=self.beam_width_deg)
        else:
            poly = _clip(self.coverage_polygon(to_chart), sea_clip)
        polys = _polys_of(poly)
        if polys:
            gpd.GeoSeries(polys).plot(
                ax=ax, facecolor=color, edgecolor=color,
                alpha=alpha, linewidth=1.6, zorder=6,
            )
            if self.outline:
                for p in polys:
                    ax.plot(*p.exterior.xy, color=color, lw=1.6, ls="--", zorder=6.5)
        ax.scatter([cx], [cy], s=50, marker="D", color=color,
                   edgecolor="black", linewidth=0.8, zorder=7)
        text = label_override if label_override is not None else self.label
        ax.annotate(text, xy=(cx, cy), xytext=(8, 8),
                    textcoords="offset points", fontsize=label_fontsize,
                    fontweight="bold", color=color, zorder=8,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                              edgecolor="#bbb", linewidth=0.4, alpha=0.95))

    def legend_text(self) -> str:
        beam = "omni" if self.beam_width_deg >= 360 else f"{self.beam_width_deg:.0f}° sector"
        short = " ".join(self.label.split())
        return f"{short} — {self.range_m:.0f} m active, {beam}"


def _wedge_polygon(cx, cy, range_m, bearing_deg, beam_width_deg, n=64):
    """Sector polygon centered at (cx, cy) in metric chart coords.

    bearing_deg is compass (0=N, 90=E). beam_width_deg is the full angular
    span. The result includes the apex so the wedge fills back to centre.
    """
    half = beam_width_deg / 2.0
    angles = np.linspace(bearing_deg - half, bearing_deg + half, n)
    math_angles = np.deg2rad(90.0 - angles)
    arc_x = cx + range_m * np.cos(math_angles)
    arc_y = cy + range_m * np.sin(math_angles)
    coords = [(cx, cy)] + list(zip(arc_x, arc_y)) + [(cx, cy)]
    return Polygon(coords)


@dataclass
class PassiveNode:
    """Single passive hydrophone pod with a stylized sensitivity envelope.

    Drawn as a filled dot + transparent range circle. The circle is the
    single-pod sensitivity envelope **against a stated target class** (a
    vessel circle ≠ a diver circle for the same pod), not a measured
    detection footprint. See METHODOLOGY.md §6.

    Design use: place pods one at a time, using the s = r rule of thumb —
    next pod at the edge of the previous circle gives cross-bearing
    geometry (METHODOLOGY.md §8).

    `outline=True` (default) draws a dashed edge. Set `False` for filled
    circle only.
    """
    center: LatLon
    range_m: float
    label: str
    target_class: Optional[str] = None      # informational: 'vessel', 'sub', 'diver/UUV'
    color: Optional[str]   = None
    alpha: Optional[float] = None
    outline: bool          = True

    def draw(self, ax, to_chart: Callable, *, sea_clip=None,
              land_obstacles=None,
              label_fontsize: float = 10,
              label_override: Optional[str] = None, **_):
        c, a = DEFAULT_COLORS["passive"]
        color, alpha = self.color or c, self.alpha if self.alpha is not None else a
        lat, lon = self.center
        cx, cy = to_chart(lon, lat)
        # Visibility polygon (acoustic line-of-sight) when land is known;
        # simple sea_clip intersection otherwise.
        if land_obstacles is not None:
            circle = _visibility_polygon(cx, cy, self.range_m, land_obstacles)
        else:
            circle = _clip(Point(cx, cy).buffer(self.range_m, resolution=64),
                            sea_clip)
        polys = _polys_of(circle)
        if polys:
            gpd.GeoSeries(polys).plot(
                ax=ax, facecolor=color, edgecolor=color,
                alpha=alpha, linewidth=1.4, zorder=6,
            )
            if self.outline:
                for p in polys:
                    ax.plot(*p.exterior.xy, color=color, lw=1.4, ls="--", zorder=6.5)
        ax.scatter([cx], [cy], s=44, marker="o", color=color,
                   edgecolor="black", linewidth=0.8, zorder=7)
        text = label_override if label_override is not None else self.label
        ax.annotate(text, xy=(cx, cy), xytext=(8, 8),
                    textcoords="offset points", fontsize=label_fontsize,
                    fontweight="bold", color=color, zorder=8,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                              edgecolor="#bbb", linewidth=0.4, alpha=0.95))

    def legend_text(self) -> str:
        tc = f" vs. {self.target_class}" if self.target_class else ""
        short = " ".join(self.label.split())
        return f"{short} — {self.range_m:.0f} m passive{tc}"


@dataclass
class PassiveArray:
    """Cabled / fiber-optic passive hydrophone array — N nodes, geometric coverage.

    Coverage is drawn as a Voronoi diagram clipped to a bounding bbox so each
    node owns its "nearest-point" responsibility region. This is the honest
    way to depict a passive array — there's no single "detection range" that
    means much; what matters is the spatial layout of the nodes.
    """
    nodes: Sequence[LatLon]         # each (lat, lon)
    label: str
    color: Optional[str]   = None
    alpha: Optional[float] = None

    def draw(self, ax, to_chart: Callable, *, sea_clip=None,
              clip_bbox: Optional[Tuple[float, float, float, float]] = None,
              label_fontsize: float = 10,
              label_override: Optional[str] = None):
        c, a = DEFAULT_COLORS["passive"]
        color, alpha = self.color or c, self.alpha if self.alpha is not None else a

        pts_chart = [Point(*to_chart(lon, lat)) for lat, lon in self.nodes]
        if len(pts_chart) >= 2:
            mp = unary_union(pts_chart)
            try:
                voro = voronoi_diagram(mp, envelope=box(*clip_bbox) if clip_bbox else None)
                cells = list(voro.geoms)
                if clip_bbox:
                    clip = box(*clip_bbox)
                    cells = [cell.intersection(clip) for cell in cells]
                if sea_clip is not None:
                    cells = [cell.intersection(sea_clip) for cell in cells]
                cells = [cell for cell in cells if not cell.is_empty]
                if cells:
                    gpd.GeoSeries(cells).plot(
                        ax=ax, facecolor=color, edgecolor=color,
                        alpha=alpha, linewidth=0.8, zorder=6,
                    )
            except Exception:
                # voronoi_diagram fails for collinear nodes — skip the cells,
                # still draw the nodes.
                pass

        xs = [p.x for p in pts_chart]
        ys = [p.y for p in pts_chart]
        ax.scatter(xs, ys, s=36, marker="o", color=color,
                   edgecolor="black", linewidth=0.8, zorder=7)
        if pts_chart:
            cx = sum(p.x for p in pts_chart) / len(pts_chart)
            cy = sum(p.y for p in pts_chart) / len(pts_chart)
            text = label_override if label_override is not None else self.label
            ax.annotate(text, xy=(cx, cy), xytext=(0, -14),
                        textcoords="offset points", ha="center", fontsize=label_fontsize,
                        fontweight="bold", color=color, zorder=8,
                        bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                                  edgecolor="none", alpha=0.85))

    def legend_text(self) -> str:
        short = " ".join(self.label.split())
        return f"{short} — passive array ({len(self.nodes)} nodes, Voronoi)"


@dataclass
class SurveySwath:
    """Survey-sonar coverage along a track (e.g. Norbit multibeam).

    `track` is a sequence of (lat, lon) waypoints; `swath_m` is the across-
    track swath width. Drawn as the line buffered to half-swath each side.
    """
    track: Sequence[LatLon]         # each (lat, lon)
    swath_m: float
    label: str
    color: Optional[str]   = None
    alpha: Optional[float] = None

    def draw(self, ax, to_chart: Callable, *, sea_clip=None,
              label_fontsize: float = 10,
              label_override: Optional[str] = None, **_):
        c, a = DEFAULT_COLORS["survey"]
        color, alpha = self.color or c, self.alpha if self.alpha is not None else a

        coords = [to_chart(lon, lat) for lat, lon in self.track]
        if len(coords) < 2:
            return
        line = LineString(coords)
        swath = _clip(line.buffer(self.swath_m / 2.0, cap_style=2), sea_clip)
        polys = _polys_of(swath)
        if polys:
            gpd.GeoSeries(polys).plot(
                ax=ax, facecolor=color, edgecolor=color,
                alpha=alpha, linewidth=1.2, zorder=6,
            )
        ax.plot([c[0] for c in coords], [c[1] for c in coords],
                color=color, lw=1.6, zorder=6.5)
        text = label_override if label_override is not None else self.label
        mid = coords[len(coords) // 2]
        ax.annotate(text, xy=mid, xytext=(8, 8),
                    textcoords="offset points", fontsize=label_fontsize,
                    fontweight="bold", color=color, zorder=8,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                              edgecolor="#bbb", linewidth=0.4, alpha=0.95))

    def legend_text(self) -> str:
        short = " ".join(self.label.split())
        return f"{short} — swath {self.swath_m:.0f} m"


# ── Annotation primitives ──────────────────────────────────────────────────
# Not sensors — geometry primitives charted alongside sensors. Same
# .draw(ax, to_chart) interface so chart.py iterates them uniformly.

@dataclass
class TextLabel:
    """Free-form text label at a single position.

    Use for non-sensor annotations: "FPSO HQ", "PROHIBITED ZONE", incident
    references, navigation notes, etc.
    """
    position: LatLon                # (lat, lon)
    text: str
    color: Optional[str] = None
    fontsize: int = 11

    def draw(self, ax, to_chart: Callable, *, label_fontsize: float = 10,
              label_override: Optional[str] = None, **_):
        c = self.color or DEFAULT_COLORS["label"][0]
        lat, lon = self.position
        cx, cy = to_chart(lon, lat)
        text = label_override if label_override is not None else self.text
        ax.text(cx, cy, text, ha="center", va="center",
                fontsize=self.fontsize, fontweight="bold", color=c, zorder=8,
                bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                          edgecolor="#bbb", linewidth=0.5, alpha=0.92))

    def legend_text(self):
        return None  # annotation — not in the sensor legend


# ── Measurement primitives ─────────────────────────────────────────────────
# Lengths and areas are computed in chart CRS (already metric, thanks to
# the local-TMerc default) so the printed values match the chart's rulers
# exactly — no haversine drift. legend_text() returns None: measurements
# are self-labelled on the chart, not in the sensor legend.

@dataclass
class DistanceLine:
    """Measurement: polyline length drawn as a CAD-style dimension line.

    Arrows at both endpoints + length labelled at midpoint. Multi-segment
    polylines show the total length only (split into separate measurements
    to label segments individually).

    `label` overrides the auto-formatted value — use for things like
    "channel width" or "approach distance".
    """
    vertices: Sequence[LatLon]      # ≥2 points (lat, lon)
    label: Optional[str] = None
    color: str = MEASURE_COLOR

    def draw(self, ax, to_chart: Callable, *, label_fontsize: float = 10,
              **_):
        if len(self.vertices) < 2:
            return
        coords = [to_chart(lon, lat) for lat, lon in self.vertices]
        # Total length in chart CRS metres
        total_m = sum(
            ((coords[i+1][0] - coords[i][0]) ** 2
             + (coords[i+1][1] - coords[i][1]) ** 2) ** 0.5
            for i in range(len(coords) - 1)
        )
        # Polyline core
        ax.plot([p[0] for p in coords], [p[1] for p in coords],
                color=self.color, lw=1.4, zorder=5)
        # CAD-style arrowheads at each end (pointing outward along the
        # line direction at that end).
        for a, b in [(coords[1], coords[0]),
                     (coords[-2], coords[-1])]:
            ax.annotate("", xy=b, xytext=a,
                        arrowprops=dict(arrowstyle="-|>",
                                         color=self.color, lw=1.4,
                                         shrinkA=0, shrinkB=0))
        # Label at midpoint, offset perpendicular for legibility (so it
        # doesn't sit on top of the line). For multi-segment polylines we
        # use the centroid of vertices — less perfect but never collides
        # with the line itself.
        if len(coords) == 2:
            mx = (coords[0][0] + coords[1][0]) / 2
            my = (coords[0][1] + coords[1][1]) / 2
            dx = coords[1][0] - coords[0][0]
            dy = coords[1][1] - coords[0][1]
            seg_len = (dx * dx + dy * dy) ** 0.5
            if seg_len > 0:
                # perpendicular unit vector × 4% of segment length
                lx = mx + (-dy / seg_len) * seg_len * 0.04
                ly = my + ( dx / seg_len) * seg_len * 0.04
            else:
                lx, ly = mx, my
        else:
            lx = sum(p[0] for p in coords) / len(coords)
            ly = sum(p[1] for p in coords) / len(coords)

        text = self.label or _format_distance(total_m)
        ax.text(lx, ly, text, ha="center", va="center",
                fontsize=label_fontsize - 1, fontweight="bold",
                color=self.color, zorder=6,
                bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                          edgecolor="#bbb", linewidth=0.4, alpha=0.95))

    def legend_text(self):
        return None


@dataclass
class AreaPolygon:
    """Measurement: closed-polygon area, translucent fill + label.

    Same fill convention as `ZonePolygon` (12% alpha) for visual
    consistency. Label sits at the polygon centroid. Override `label`
    for a custom string ("exclusion zone", "AOI sub-region", etc.).
    """
    vertices: Sequence[LatLon]      # ≥3 points (lat, lon)
    label: Optional[str] = None
    color: str = MEASURE_COLOR
    fill_alpha: float = 0.12

    def draw(self, ax, to_chart: Callable, *, label_fontsize: float = 10,
              **_):
        if len(self.vertices) < 3:
            return
        coords = [to_chart(lon, lat) for lat, lon in self.vertices]
        poly = Polygon(coords)
        gpd.GeoSeries([poly]).plot(
            ax=ax, facecolor=self.color, edgecolor="none",
            alpha=self.fill_alpha, zorder=5)
        # Solid outline at full opacity
        xs = [p[0] for p in coords] + [coords[0][0]]
        ys = [p[1] for p in coords] + [coords[0][1]]
        ax.plot(xs, ys, color=self.color, lw=1.4, zorder=5.5)
        # Label at centroid
        cx, cy = poly.centroid.x, poly.centroid.y
        text = self.label or _format_area(poly.area)
        ax.text(cx, cy, text, ha="center", va="center",
                fontsize=label_fontsize - 1, fontweight="bold",
                color=self.color, zorder=6,
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                          edgecolor="#bbb", linewidth=0.4, alpha=0.95))

    def legend_text(self):
        return None


_LINESTYLES = {
    "solid":  "-",
    "dashed": (0, (6, 3)),
    "dotted": (0, (1, 3)),
}


@dataclass
class ZonePolygon:
    """Closed polygon with optional fill and label.

    For threat corridors, exclusion zones, no-go areas, AOI sub-regions.
    `linestyle` ∈ {"solid", "dashed", "dotted"}.
    """
    vertices: Sequence[LatLon]      # ≥3 points (lat, lon)
    label: str = ""
    color: Optional[str]   = None
    alpha: Optional[float] = None
    fill: bool             = True
    linestyle: str         = "solid"

    def draw(self, ax, to_chart: Callable, *, label_fontsize: float = 10,
              label_override: Optional[str] = None, **_):
        if len(self.vertices) < 3:
            return
        c, default_a = DEFAULT_COLORS["zone"]
        color = self.color or c
        alpha = self.alpha if self.alpha is not None else default_a
        ls = _LINESTYLES.get(self.linestyle, "-")

        coords = [to_chart(lon, lat) for lat, lon in self.vertices]
        if self.fill:
            poly = Polygon(coords)
            gpd.GeoSeries([poly]).plot(ax=ax, facecolor=color, edgecolor="none",
                                        alpha=alpha, zorder=4)
        xs = [p[0] for p in coords] + [coords[0][0]]
        ys = [p[1] for p in coords] + [coords[0][1]]
        ax.plot(xs, ys, color=color, lw=1.8, ls=ls, zorder=5)

        text = label_override if label_override is not None else self.label
        if text:
            cx = sum(p[0] for p in coords) / len(coords)
            cy = sum(p[1] for p in coords) / len(coords)
            ax.annotate(text, xy=(cx, cy), xytext=(0, 0),
                         textcoords="offset points", ha="center", va="center",
                         fontsize=label_fontsize, fontweight="bold", color=color,
                         bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                                   edgecolor="#bbb", linewidth=0.4, alpha=0.92),
                         zorder=6)

    def legend_text(self):
        return None  # annotation — not in the sensor legend
