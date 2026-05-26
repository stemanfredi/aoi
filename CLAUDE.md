# AOI — agent map

Coastal sensor-siting chart engine. Frame an AOI, render a publication-
grade nautical chart (bathy + terrain + coastline + sensor overlays +
measurements). One artifact, one publication look.

User-facing intro and quick-start: [README.md](./README.md).
Siting doctrine, range derating, vendor matrix: [METHODOLOGY.md](./METHODOLOGY.md).
This file: architecture and where to touch what.

## File map

Flat layout; six modules + the notebook.

| File | Role |
|---|---|
| `session.py`   | `AOISession`: frame state + `AOICache` of fetched/reprojected data. The central state container. |
| `render.py`    | matplotlib renderer (`render_from_cache`, all chart-element helpers). `coastal_chart()` is the one-shot wrapper. `ChartStyle` lives here. |
| `drawables.py` | Sensor archetypes (`ActiveSonar`, `PassiveNode`, `PassiveArray`, `SurveySwath`), annotations (`TextLabel`, `ZonePolygon`), measurements (`DistanceLine`, `AreaPolygon`). All conform to `.draw(ax, to_chart, *, sea_clip, land_obstacles, …)`. |
| `presets.py`   | Vendor preset catalog + `build_sensor()`. |
| `fetch.py`     | All data fetchers (bathy / OSMData land / SRTM terrain / OSM features). Disk-cached. |
| `designer.py`  | ipyleaflet UI; uses `AOISession` directly. |
| `designer.ipynb` | 3-cell launcher. |

Gitignored: `cache/` (incl. the ~1.3 GB OSMData shapefile), `images/`,
`.env` (holds `OPENTOPOGRAPHY_API_KEY`; template in `.env.example`).

## Architecture: fetch-once-render-many

`AOISession` separates the slow part (network I/O + reprojection) from
the fast part (matplotlib). Multiple renders against one session reuse
the cache; only frame-moving updates invalidate it.

```python
from session import AOISession
s = AOISession(name="my_aoi", center=(lat, lon), half_w_km=8.0)
s.fetch()                           # parallel fetch + reproject, ~3-5 s cold
s.render(drawables=[])              # ~0.4 s
s.render(drawables=all_sensors)     # ~0.4 s
s.update(center=(other_lat, other_lon))   # cache dropped, next render re-fetches
```

The designer uses one shared session for Preview + Save — Save no
longer re-pays the fetch cost.

`coastal_chart(name, center, half_w_km, drawables=…)` (render.py) is a
thin wrapper around session-fetch-render for one-shot scripts.

## Data sources

| Source | Module | Auth | Cache |
|---|---|---|---|
| Bathymetry — EMODnet DTM 2024 (EU/Caribbean) → GEBCO 2020 (global) | `fetch.fetch_bathy` | none | `cache/bathy_{name}_{tag}.nc` |
| Land/sea boundary — OSMData land-polygons-complete-4326 (single authority) | `fetch.fetch_land_polygons` | none | `cache/osm_land_polygons/` (auto-downloaded ~700 MB) |
| Terrain — SRTM 30 m via OpenTopography | `fetch.fetch_terrain` | `OPENTOPOGRAPHY_API_KEY` (free) | `cache/terrain_{name}.tif` |
| OSM features — inland water, place names, port piers, streets | `fetch.fetch_*` | none | osmnx HTTP cache |

`AOISession.fetch()` dispatches all fetches concurrently, then all
reprojections concurrently — both stages release the GIL during heavy
I/O / NumPy ops.

## Land/sea reconciliation

OSMData land polygons are the single authority. The render:

1. Builds `land_geom = union(land_xy)` clipped to the chart bbox.
2. Paints buff land (axes facecolor) + white sea (bbox minus land).
3. Masks bathy to sea (no isobaths on land), terrain to land (no topo
   lines in the water).
4. Single-point sensors (`ActiveSonar`, `PassiveNode`) cast a
   visibility polygon from the sensor centre against `land_geom` —
   coverage stops at the first land obstacle. `SurveySwath` and
   `PassiveArray` use plain sea_clip (line-of-sight from one point
   doesn't apply to them).

The OSMData feature is bbox-clipped in EPSG:4326 *before* reprojection
(continent-scale multipolygons would blow up local TMerc).

## Projection

`AOISession` auto-picks a local Transverse Mercator centred on the
AOI → geographic north is vertical at the centre. Pass `epsg=` for a
fixed UTM zone instead.

## ChartStyle

~25 cosmetic + visibility fields on a dataclass in `render.py`. Layer
toggles (`show_isobaths`, `show_coastline`, `show_terrain_contours`,
etc.) skip render steps when False. `emphasize_major_contours=False`
flattens majors to the same colour + lw + alpha as minors (only labels
differ).

The designer exposes the toggles as grouped checkboxes (Sea / Land /
Chart frame / Emphasis).

## Where to touch

| Change | Where |
|---|---|
| New sensor archetype | `drawables.py` (one dataclass + `.draw()` + `.legend_text()`) |
| New data source | `fetch.py` (one function); wire into `AOISession.fetch` and `AOICache` |
| New rendering step | inside `render._render_to` — match the existing pattern (style.show_X check, draw to `ax`) |
| New visibility-affecting layer | extend `_draw_sensors` plumbing if the new layer should clip / occlude sensor coverage |
| New ChartStyle field | add to `ChartStyle` in `render.py`; if user-facing, add a checkbox in the designer's `_layer_groups` |

## What stays out

See METHODOLOGY.md §12 for the explicit deferred list (TL solver,
sound-speed profile, bottom-type derate, structured per-target ranges,
BarrierBoom primitive, etc.). Don't promote anything from there until
a real chart needs it.
