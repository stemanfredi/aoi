"""Data fetchers — bathymetry, land polygons, terrain, OSM features.

All sources are disk-cached under `cache/`. Fetchers are deliberately
thin and side-effect-free at module import time; AOISession dispatches
them concurrently via ThreadPoolExecutor.

Sources (none require a paid key):
- Bathymetry: EMODnet DTM 2024 (Europe / Caribbean) → GEBCO 2020 (global).
  ERDDAP servers, no key.
- Land/sea boundary: OSMData `land-polygons-complete-4326` (one-time
  ~700 MB download, ODbL, source https://osmdata.openstreetmap.de).
- Terrain: SRTM 30 m via OpenTopography. Needs OPENTOPOGRAPHY_API_KEY in
  .env. Skipped silently when missing or quota exhausted.
- OSM features (inland water, port piers, streets, place names): osmnx.
"""
from pathlib import Path
import os

import osmnx as ox
import xarray as xr

CACHE = Path(__file__).resolve().parent / "cache"
CACHE.mkdir(exist_ok=True)

# osmnx — use our cache directory, stay quiet
ox.settings.cache_folder = str(CACHE)
ox.settings.use_cache = True
ox.settings.log_console = False


# ── Bathymetry ─────────────────────────────────────────────────────────────

_EMODNET_EU    = (-36.0, 43.0,  15.0, 90.0)     # (wll, ell, sll, nll)
_EMODNET_CARIB = (-78.0, -58.0, 8.0,  28.0)

GEBCO_URL   = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/GEBCO_2020"
EMODNET_URL = "https://erddap.emodnet.eu/erddap/griddap/{dataset_id}"

SOURCE_CITATION = {
    "gebco_2020":         "GEBCO 2020 Grid (15 arc-sec) — NOAA CoastWatch ERDDAP",
    "emodnet_2024":       "EMODnet DTM 2024 (1/16 arc-min) — emodnet.ec.europa.eu",
    "emodnet_2024_carib": "EMODnet DTM Caribbean 2024 (1/16 arc-min) — emodnet.ec.europa.eu",
}


def _bbox_inside(wll, ell, sll, nll, env):
    ew, ee, es, en = env
    return wll >= ew and ell <= ee and sll >= es and nll <= en


def _route_bathy(wll, ell, sll, nll):
    """Pick a bathy source for a bbox. Returns (tag, dataset_id_or_url)."""
    if _bbox_inside(wll, ell, sll, nll, _EMODNET_EU):
        return "emodnet_2024", "bathymetry_dtm_2024"
    if _bbox_inside(wll, ell, sll, nll, _EMODNET_CARIB):
        return "emodnet_2024_carib", "bathymetry_dtm_carib_2024"
    return "gebco_2020", GEBCO_URL


def _covered(ds, sll, nll, wll, ell, pad):
    return (float(ds.latitude.min())  <= sll - pad and
            float(ds.latitude.max())  >= nll + pad and
            float(ds.longitude.min()) <= wll - pad and
            float(ds.longitude.max()) >= ell + pad)


def _fetch_erddap(url, sll, nll, wll, ell, pad):
    """Subset an ERDDAP griddap dataset. Raises if the window has no data."""
    ds = xr.open_dataset(url).sel(
        latitude=slice(sll - pad, nll + pad),
        longitude=slice(wll - pad, ell + pad),
    )
    ds.load()
    if ds.latitude.size == 0 or ds.longitude.size == 0:
        raise ValueError(f"empty subset from {url}")
    return ds


def fetch_bathy(name, sll, nll, wll, ell, *, pad=0.03, source="auto"):
    """Fetch bathymetry. Returns (xarray.Dataset, source_tag).

    `elevation` is in metres (negative below sea level). Caches to
    cache/bathy_{name}_{source}.nc.
    """
    if source == "auto":
        tag, target = _route_bathy(wll, ell, sll, nll)
    elif source == "gebco":
        tag, target = "gebco_2020", GEBCO_URL
    elif source == "emodnet":
        tag, target = "emodnet_2024", "bathymetry_dtm_2024"
    else:
        raise ValueError(f"unknown source: {source!r}")

    cache_nc = CACHE / f"bathy_{name}_{tag}.nc"
    if cache_nc.exists():
        with xr.open_dataset(cache_nc) as ds_c:
            if _covered(ds_c, sll, nll, wll, ell, pad):
                return xr.open_dataset(cache_nc).sel(
                    latitude=slice(sll - pad, nll + pad),
                    longitude=slice(wll - pad, ell + pad),
                ), tag
        cache_nc.unlink()

    url = EMODNET_URL.format(dataset_id=target) if tag.startswith("emodnet") else target

    try:
        ds = _fetch_erddap(url, sll, nll, wll, ell, pad)
    except Exception as e:
        if tag == "gebco_2020":
            raise
        print(f"  {tag} failed ({e}); falling back to GEBCO 2020")
        tag = "gebco_2020"
        cache_nc = CACHE / f"bathy_{name}_{tag}.nc"
        if cache_nc.exists():
            with xr.open_dataset(cache_nc) as ds_c:
                if _covered(ds_c, sll, nll, wll, ell, pad):
                    return xr.open_dataset(cache_nc).sel(
                        latitude=slice(sll - pad, nll + pad),
                        longitude=slice(wll - pad, ell + pad),
                    ), tag
        ds = _fetch_erddap(GEBCO_URL, sll, nll, wll, ell, pad)

    ds.to_netcdf(cache_nc)
    return ds, tag


# ── OSMData land polygons (the single land/sea authority) ──────────────────
#
# Two datasets, chosen at call time by the AOI_SIMPLIFIED_LAND env var:
#   unset / "0"  → land-polygons-complete-4326 (full detail, WGS84,
#                  ~700 MB ZIP / ~1.3 GB on disk). The local default.
#   "1"          → simplified-land-polygons-complete-3857 (~12 MB ZIP,
#                  Web Mercator, coarser coastline). For memory-limited
#                  hosts like Streamlit Community Cloud's free tier.

LAND_DIR = CACHE / "osm_land_polygons"


def _land_dataset():
    """Return (url, subdir_name, shp_name, epsg) for the active dataset."""
    if os.environ.get("AOI_SIMPLIFIED_LAND", "0") == "1":
        return (
            "https://osmdata.openstreetmap.de/download/"
            "simplified-land-polygons-complete-3857.zip",
            "simplified-land-polygons-complete-3857",
            "simplified_land_polygons.shp",
            3857,
        )
    return (
        "https://osmdata.openstreetmap.de/download/"
        "land-polygons-complete-4326.zip",
        "land-polygons-complete-4326",
        "land_polygons.shp",
        4326,
    )


def download_land_polygons(force=False):
    """Download the active OSMData land-polygons dataset.

    One-time; cached under cache/osm_land_polygons/. `force=True`
    re-downloads. Source + license (ODbL): https://osmdata.openstreetmap.de
    """
    import urllib.request
    import zipfile
    import shutil

    url, subdir_name, shp_name, _ = _land_dataset()
    shp = LAND_DIR / shp_name
    if shp.exists() and not force:
        return shp

    LAND_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = LAND_DIR / "land.zip"

    size_hint = "~12 MB simplified" if "simplified" in url else "~700 MB full"
    print(f"  OSMData: downloading land polygons ({size_hint}, one-time) …")
    print(f"    {url}")
    urllib.request.urlretrieve(url, zip_path)

    print(f"  OSMData: extracting …")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(LAND_DIR)

    subdir = LAND_DIR / subdir_name
    if subdir.is_dir():
        for f in subdir.iterdir():
            target = LAND_DIR / f.name
            if target.exists():
                target.unlink()
            shutil.move(str(f), str(target))
        subdir.rmdir()

    zip_path.unlink()
    print(f"  OSMData: cached at {shp}")
    return shp


def fetch_land_polygons(sll, nll, wll, ell):
    """Subset OSMData land polygons for the bbox.

    Returns a GeoDataFrame in EPSG:4326, geometries **clipped to the
    bbox**. The clipping is essential — OSMData ships continent-scale
    multipolygons; reprojecting those whole to a local TMerc produces
    infinite coordinates. Clip in lat/lon first. The simplified dataset
    ships in EPSG:3857, so we reproject the query bbox to read it and
    bring the results back to 4326.

    Auto-downloads the active dataset on first call.
    """
    import geopandas as gpd
    from shapely.geometry import box as shp_box
    _, _, shp_name, epsg = _land_dataset()
    shp = LAND_DIR / shp_name
    if not shp.exists():
        download_land_polygons()

    if epsg == 4326:
        gdf = gpd.read_file(shp, bbox=(wll, sll, ell, nll))
    else:
        from pyproj import Transformer
        t = Transformer.from_crs(4326, epsg, always_xy=True)
        x_min, y_min = t.transform(wll, sll)
        x_max, y_max = t.transform(ell, nll)
        gdf = gpd.read_file(shp, bbox=(x_min, y_min, x_max, y_max))
        if not gdf.empty:
            gdf = gdf.to_crs(4326)

    if gdf.empty:
        return gdf
    bbox = shp_box(wll, sll, ell, nll)
    return gpd.GeoDataFrame(
        geometry=[g.intersection(bbox) for g in gdf.geometry],
        crs=4326,
    )


# ── Terrain (SRTM 30 m via OpenTopography) ─────────────────────────────────

OT_URL = "https://portal.opentopography.org/API/globaldem"


def _ot_key():
    """Read the OpenTopography API key from env or repo .env file."""
    key = os.environ.get("OPENTOPOGRAPHY_API_KEY")
    if key:
        return key.strip()
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("OPENTOPOGRAPHY_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def fetch_terrain(name, sll, nll, wll, ell, pad=0.05):
    """Return xarray.DataArray of SRTM 30 m elevations for the bbox, or None.

    None signals "no terrain available" (no API key, fetch failed, polar
    latitude outside SRTM coverage, etc.) — callers must handle that and
    proceed with bathy-only rendering.
    """
    import rioxarray  # noqa: F401  (registers .rio accessor)
    import requests

    cache_file = CACHE / f"terrain_{name}.tif"

    if cache_file.exists():
        try:
            da = rioxarray.open_rasterio(cache_file, masked=True).squeeze()
            x = da.x.values
            y = da.y.values
            covers = (x.min() <= wll - pad and x.max() >= ell + pad
                      and y.min() <= sll - pad and y.max() >= nll + pad)
            if covers:
                return da
        except Exception:
            pass  # corrupt cache → re-fetch

    key = _ot_key()
    if not key:
        print("  terrain: skipping (no OpenTopography key). "
              "Free key at https://opentopography.org/developers — "
              "paste into .env as OPENTOPOGRAPHY_API_KEY=...")
        return None

    params = {
        "demtype":      "SRTMGL1",
        "south":        sll - pad,
        "north":        nll + pad,
        "west":         wll - pad,
        "east":         ell + pad,
        "outputFormat": "GTiff",
        "API_Key":      key,
    }
    print(f"  terrain: fetching SRTM 30m via OpenTopography "
          f"(bbox {sll - pad:.2f},{wll - pad:.2f} → {nll + pad:.2f},{ell + pad:.2f})")
    try:
        r = requests.get(OT_URL, params=params, timeout=180)
        r.raise_for_status()
        if not r.content.startswith(b"II") and not r.content.startswith(b"MM"):
            print(f"  terrain: unexpected response: {r.text[:200]}")
            return None
    except Exception as exc:
        print(f"  terrain: fetch failed: {exc}")
        return None

    cache_file.write_bytes(r.content)
    try:
        return rioxarray.open_rasterio(cache_file, masked=True).squeeze()
    except Exception as exc:
        print(f"  terrain: failed to open downloaded raster: {exc}")
        return None


# ── OSM features (osmnx) ───────────────────────────────────────────────────
# All four fetchers below share the same robustness pattern: a per-query
# disk cache (GeoPackage) survives Overpass outages, and we retry the
# network call a few times with backoff for transient blips. The cache
# key hashes the query parameters (bbox or point + radius), so identical
# queries hit the cache; panning to a new frame triggers a fresh fetch.

import hashlib


def _bbox_key(sll, nll, wll, ell):
    return hashlib.md5(
        f"{sll:.4f}_{nll:.4f}_{wll:.4f}_{ell:.4f}".encode()
    ).hexdigest()[:10]


def _point_key(lat, lon, dist_m):
    return hashlib.md5(
        f"{lat:.4f}_{lon:.4f}_{int(dist_m)}".encode()
    ).hexdigest()[:10]


def _osm_cached(prefix, key, query_fn, *, retries=3, backoff_s=1.5):
    """Disk-cache + retry wrapper for an osmnx query returning a GeoDataFrame.

    Cache file: cache/{prefix}_{key}.gpkg. Once a non-empty result is
    written, subsequent calls hit the disk and never touch the network —
    Venice lagoon renders correctly even when Overpass is down.
    """
    import geopandas as gpd
    import time

    cache_file = CACHE / f"{prefix}_{key}.gpkg"
    if cache_file.exists():
        try:
            return gpd.read_file(cache_file)
        except Exception:
            cache_file.unlink()

    err = None
    for attempt in range(retries):
        try:
            result = query_fn()
            if not result.empty:
                # GeoPackage doesn't like list-typed columns OSM occasionally
                # carries (e.g. multiple values per tag). Drop them.
                cols = [c for c in result.columns
                        if c == "geometry"
                        or not result[c].apply(lambda v: isinstance(v, list)).any()]
                result[cols].to_file(cache_file, driver="GPKG")
            return result
        except Exception as e:
            err = e
            if attempt < retries - 1:
                time.sleep(backoff_s * (attempt + 1))

    print(f"  {prefix} fetch failed after {retries} attempts: {err}")
    return gpd.GeoDataFrame(geometry=[], crs=4326)


def fetch_inland_water(sll, nll, wll, ell):
    """OSM `natural=water` + `landuse=reservoir` polygons inside the bbox.

    Rendered white on top of the buff land — lakes, reservoirs, lagoons
    that aren't part of the global sea but should read as water (Venice
    lagoon, Bahía de Cádiz, etc.).
    """
    def _q():
        gdf = ox.features_from_bbox(
            (wll, sll, ell, nll),
            tags={"natural": "water", "landuse": "reservoir"},
        )
        return gdf[gdf.geom_type.isin(["Polygon", "MultiPolygon"])]
    return _osm_cached("inland_water", _bbox_key(sll, nll, wll, ell), _q)


def fetch_port_features(lat, lon, dist_m):
    """OSM piers / breakwaters / quays / groynes and buildings around (lat, lon).

    Returns (piers_gdf, buildings_gdf) in EPSG:4326. Either may be empty.
    """
    key = _point_key(lat, lon, dist_m)

    def _piers_q():
        return ox.features_from_point(
            (lat, lon), dist=dist_m,
            tags={"man_made": ["breakwater", "pier", "quay", "groyne"]},
        )

    def _bldgs_q():
        return ox.features_from_point(
            (lat, lon), dist=dist_m, tags={"building": True},
        )

    return (
        _osm_cached("piers",     key, _piers_q),
        _osm_cached("buildings", key, _bldgs_q),
    )


def fetch_place_names(lat, lon, dist_m):
    """OSM `place=*` features (city/town/village/suburb/hamlet) around (lat, lon).

    Returns a GeoDataFrame with a `name` column and Point geometries in
    EPSG:4326. Skips island/region/country — too coarse for port scale.
    """
    def _q():
        gdf = ox.features_from_point(
            (lat, lon), dist=dist_m,
            tags={"place": ["city", "town", "village", "suburb", "hamlet"]},
        )
        gdf = gdf[gdf.geom_type == "Point"]
        return gdf[["name", "place", "geometry"]] if "name" in gdf.columns else gdf
    return _osm_cached("places", _point_key(lat, lon, dist_m), _q)


def fetch_streets(lat, lon, dist_m):
    """OSM road-network LineStrings around (lat, lon).

    Motorway → residential + service; skips footways/cycleways/paths/tracks
    (too noisy at port scale).
    """
    def _q():
        gdf = ox.features_from_point(
            (lat, lon), dist=dist_m,
            tags={"highway": ["motorway", "trunk", "primary", "secondary",
                              "tertiary", "unclassified", "residential",
                              "service"]},
        )
        return gdf[gdf.geom_type.isin(["LineString", "MultiLineString"])]
    return _osm_cached("streets", _point_key(lat, lon, dist_m), _q)
