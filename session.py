"""AOISession — fetch-once-render-many.

Encapsulates an AOI frame (centre + half-width + aspect + projection)
plus all the fetched/reprojected geographic data, so multiple renders
against the same frame reuse the cached layers instead of re-paying
the fetch + reprojection cost on each call.

Typical use:

    session = AOISession(name="my_aoi", center=(lat, lon), half_w_km=8)
    session.fetch()                                      # ~3-5 s cold
    session.render(drawables=[], title="preview")        # ~0.4 s
    session.render(drawables=all_sensors, title="final") # ~0.4 s

The designer's Preview and Save buttons share one session — Save costs
no extra network or reprojection work, just matplotlib.

`coastal_chart()` (in render.py) is a thin one-shot wrapper that builds
a session, fetches once, and renders. Use it for single-render scripts;
use AOISession directly when the same frame will render more than once.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple

import geopandas as gpd
import numpy as np
import rioxarray  # noqa: F401  (registers .rio accessor)
from pyproj import Transformer

from fetch import (fetch_bathy, fetch_inland_water, fetch_land_polygons,
                    fetch_place_names, fetch_port_features, fetch_streets,
                    fetch_terrain)


def _local_tmerc(lat0: float, lon0: float) -> str:
    """proj4 string for a Transverse Mercator centred on (lat0, lon0)."""
    return (f"+proj=tmerc +lat_0={lat0:.10f} +lon_0={lon0:.10f} +k=1.0 "
            f"+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")


def _reproject(da, crs, *, x_dim="longitude", y_dim="latitude"):
    """Reproject a DataArray via rioxarray (bilinear)."""
    if "spatial_ref" not in da.coords:
        da = da.rio.write_crs(4326)
    da = da.rio.set_spatial_dims(x_dim=x_dim, y_dim=y_dim, inplace=False)
    return da.rio.reproject(crs, resampling=1)


@dataclass
class AOICache:
    """All fetched + reprojected data for one AOI frame. Render-ready."""
    # Frame / projection
    chart_crs:   str
    chart_desc:  str          # human-readable projection description
    fwd:         Transformer  # 4326 → chart CRS (always_xy=True)
    inv:         Transformer  # chart CRS → 4326
    bbox:        Tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax)
    # Bathymetry
    z:           np.ndarray
    extent:      Tuple[float, float, float, float]
    src_tag:     str
    # Terrain (optional — None when SRTM unavailable)
    t_z:         Optional[np.ndarray]    = None
    t_extent:    Optional[tuple]         = None
    # Vector layers (None when absent in frame or fetch failed)
    land_xy:     Optional[gpd.GeoDataFrame] = None
    inland_xy:   Optional[gpd.GeoDataFrame] = None
    places_xy:   Optional[gpd.GeoDataFrame] = None
    piers_xy:    Optional[gpd.GeoDataFrame] = None
    bldgs_xy:    Optional[gpd.GeoDataFrame] = None
    streets_xy:  Optional[gpd.GeoDataFrame] = None


class AOISession:
    """One AOI frame + its fetched data, ready for repeated rendering."""

    def __init__(self, name: str,
                 center: Tuple[float, float],
                 half_w_km: float,
                 *,
                 aspect: float = 2 / 3,
                 epsg: Optional[int] = None,
                 source: str = "auto",
                 need_terrain: bool = True,
                 need_port: bool = True,
                 need_places: bool = True,
                 pad_deg: float = 0.05):
        # Frame parameters (mutable via .update())
        self.name        = name
        self.center      = center
        self.half_w_km   = half_w_km
        self.aspect      = aspect
        self.epsg        = epsg
        # Fetch configuration
        self.source      = source
        self.need_terrain = need_terrain
        self.need_port   = need_port
        self.need_places = need_places
        self.pad_deg     = pad_deg
        # Internal cache
        self._cache: Optional[AOICache] = None

    # ── Cache lifecycle ────────────────────────────────────────────────────

    @property
    def cache(self) -> Optional[AOICache]:
        return self._cache

    @property
    def is_fetched(self) -> bool:
        return self._cache is not None

    def invalidate(self):
        """Drop the cached data — next render will refetch."""
        self._cache = None

    def update(self, *, center=None, half_w_km=None, aspect=None,
                epsg=None, name=None) -> bool:
        """Change frame parameters. Invalidates cache only when the frame
        actually moves. Returns True if anything changed."""
        moved = False
        if center is not None and tuple(center) != tuple(self.center):
            self.center = tuple(center); moved = True
        if half_w_km is not None and half_w_km != self.half_w_km:
            self.half_w_km = half_w_km; moved = True
        if aspect is not None and aspect != self.aspect:
            self.aspect = aspect; moved = True
        if epsg is not None and epsg != self.epsg:
            self.epsg = epsg; moved = True
        if name is not None and name != self.name:
            self.name = name
            # name change doesn't move the frame, only the cache key for
            # future bathy/terrain disk caches — don't invalidate.
        if moved:
            self.invalidate()
        return moved

    # ── Frame derivation ───────────────────────────────────────────────────

    def _chart_crs(self) -> Tuple[str, str]:
        """Return (crs_string, human_description)."""
        lat0, lon0 = self.center
        if self.epsg is not None:
            return f"EPSG:{self.epsg}", f"UTM EPSG:{self.epsg}"
        return (_local_tmerc(lat0, lon0),
                f"local TMerc @ ({lat0:.4f}°, {lon0:.4f}°)")

    def _frame_bounds(self, fwd: Transformer, inv: Transformer):
        """Return ((xmin, ymin, xmax, ymax), (sll, nll, wll, ell))."""
        lat0, lon0 = self.center
        half_w_m = self.half_w_km * 1000
        half_h_m = half_w_m * self.aspect
        cx, cy = fwd.transform(lon0, lat0)
        xmin, xmax = cx - half_w_m, cx + half_w_m
        ymin, ymax = cy - half_h_m, cy + half_h_m
        corn_lon, corn_lat = inv.transform(
            np.array([xmin, xmax, xmin, xmax]),
            np.array([ymin, ymin, ymax, ymax]),
        )
        return (
            (xmin, ymin, xmax, ymax),
            (float(corn_lat.min()), float(corn_lat.max()),
             float(corn_lon.min()), float(corn_lon.max())),
        )

    # ── Fetch (parallel I/O + reprojection) ───────────────────────────────

    def fetch(self) -> AOICache:
        """Fetch all data layers concurrently + reproject to chart CRS.
        Populates and returns the cache; idempotent (no-op if already fetched).
        """
        if self._cache is not None:
            return self._cache

        chart_crs, chart_desc = self._chart_crs()
        fwd = Transformer.from_crs(4326, chart_crs, always_xy=True)
        inv = Transformer.from_crs(chart_crs, 4326, always_xy=True)
        (xmin, ymin, xmax, ymax), (sll, nll, wll, ell) = self._frame_bounds(fwd, inv)

        lat0, lon0 = self.center
        osm_dist = int((xmax - xmin) / 2 * 1.3)
        print(f"[{self.name}] fetching bathy + land + OSM + terrain in parallel …")

        # Stage 1: parallel fetch (I/O bound — Python threads happily release
        # the GIL during network + disk operations).
        with ThreadPoolExecutor(max_workers=8) as ex:
            f_bathy   = ex.submit(fetch_bathy, self.name, sll, nll, wll, ell,
                                    pad=self.pad_deg, source=self.source)
            f_land    = ex.submit(fetch_land_polygons, sll, nll, wll, ell)
            f_inland  = ex.submit(fetch_inland_water, sll, nll, wll, ell)
            f_terrain = (ex.submit(fetch_terrain, self.name, sll, nll, wll, ell,
                                     pad=self.pad_deg)
                         if self.need_terrain else None)
            f_port    = (ex.submit(fetch_port_features, lat0, lon0, osm_dist)
                         if self.need_port else None)
            f_streets = (ex.submit(fetch_streets, lat0, lon0, osm_dist)
                         if self.need_port else None)
            f_places  = (ex.submit(fetch_place_names, lat0, lon0, osm_dist)
                         if self.need_places else None)

            ds, src_tag  = f_bathy.result()
            land         = f_land.result()
            inland       = f_inland.result()
            terrain_da   = f_terrain.result() if f_terrain else None
            piers, bldgs = f_port.result() if f_port else (None, None)
            streets      = f_streets.result() if f_streets else None
            places       = f_places.result() if f_places else None

        print(f"  bathy source = {src_tag}, lon/lat shape = {ds.elevation.shape}")
        print(f"  OSMData land polygons: {len(land)} feature(s) in bbox")
        print(f"[{self.name}] reprojecting to {chart_desc} …")

        # Stage 2: reproject rasters + vectors. The rasters are heavy; do
        # them in threads too (rioxarray/rasterio release the GIL).
        def _reproject_bathy():
            r = _reproject(ds.elevation, chart_crs).rio.clip_box(xmin, ymin, xmax, ymax)
            z = r.values.astype(float)
            z = np.where(np.isfinite(z), z, np.nan)
            return z, (float(r.x.min()), float(r.x.max()),
                       float(r.y.min()), float(r.y.max()))

        def _reproject_terrain():
            if terrain_da is None:
                return None, None
            try:
                r = terrain_da.rio.reproject(chart_crs, resampling=1)
                r = r.rio.clip_box(xmin, ymin, xmax, ymax)
                t_z = r.values.astype(float)
                t_z = np.where(np.isfinite(t_z), t_z, np.nan)
                t_ext = (float(r.x.min()), float(r.x.max()),
                         float(r.y.min()), float(r.y.max()))
                print(f"  terrain: chart-CRS shape {t_z.shape}, "
                      f"elev {np.nanmin(t_z):.0f} → {np.nanmax(t_z):.0f} m")
                return t_z, t_ext
            except Exception as exc:
                print(f"  terrain: reproject/clip failed: {exc} — skipping")
                return None, None

        def _reproject_vector(gdf):
            return gdf.to_crs(chart_crs) if gdf is not None and not gdf.empty else None

        with ThreadPoolExecutor(max_workers=8) as ex:
            r_bathy   = ex.submit(_reproject_bathy)
            r_terrain = ex.submit(_reproject_terrain)
            r_land    = ex.submit(_reproject_vector, land)
            r_inland  = ex.submit(_reproject_vector, inland)
            r_places  = ex.submit(_reproject_vector, places)
            r_piers   = ex.submit(_reproject_vector, piers)
            r_bldgs   = ex.submit(_reproject_vector, bldgs)
            r_streets = ex.submit(_reproject_vector, streets)

            z, extent           = r_bathy.result()
            t_z, t_extent       = r_terrain.result()
            land_xy             = r_land.result()
            inland_xy           = r_inland.result()
            places_xy           = r_places.result()
            piers_xy            = r_piers.result()
            bldgs_xy            = r_bldgs.result()
            streets_xy          = r_streets.result()

        self._cache = AOICache(
            chart_crs=chart_crs, chart_desc=chart_desc,
            fwd=fwd, inv=inv,
            bbox=(xmin, ymin, xmax, ymax),
            z=z, extent=extent, src_tag=src_tag,
            t_z=t_z, t_extent=t_extent,
            land_xy=land_xy, inland_xy=inland_xy, places_xy=places_xy,
            piers_xy=piers_xy, bldgs_xy=bldgs_xy, streets_xy=streets_xy,
        )
        return self._cache

    # ── Render ─────────────────────────────────────────────────────────────

    def render(self, *,
                drawables: Sequence = (),
                title: Optional[str] = None,
                style=None,
                isobath_minor: Optional[Sequence[float]] = None,
                isobath_major: Optional[Sequence[float]] = None,
                label_levels: Optional[Sequence[float]] = None,
                figsize: Tuple[float, float] = (15, 10),
                dpi: int = 220,
                output_dir: Optional[Path] = None) -> Path:
        """Render the chart using the cached data. Fetches first if needed."""
        if self._cache is None:
            self.fetch()
        # Late import: render imports session-internals only at call time
        # to avoid a circular import at module load.
        from render import render_from_cache
        return render_from_cache(
            self._cache, name=self.name, center=self.center,
            drawables=drawables, title=title, style=style,
            isobath_minor=isobath_minor, isobath_major=isobath_major,
            label_levels=label_levels,
            figsize=figsize, dpi=dpi, output_dir=output_dir,
        )
