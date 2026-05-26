# AOI — Area Of Interest

Coastal sensor-siting chart generator. Frame an AOI on a Leaflet map,
drop sensor / measurement primitives, save a publication-grade PNG
(OSM coastline, IHO isobaths, SRTM land contours, sensor coverage with
acoustic line-of-sight).

The reasoning behind the design choices — doctrinal layering, range
derating, threat archetypes, cartographic conventions, vendor matrix —
is in [METHODOLOGY.md](./METHODOLOGY.md).

## Quick start

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/jupyter lab designer.ipynb
```

Frame an AOI, click **Preview bathy**, drop sensors / measurements on
the rendered chart, click **Save PNG**.

On first render the engine auto-downloads the OSMData land-polygons
shapefile (~700 MB, one-time, cached in `cache/`).

### Optional: 30 m land-terrain contours

The chart renders without this. For topographic detail on the land
side (ridgelines, valleys, 30 m contour lines), grab a free key at
[opentopography.org/developers](https://opentopography.org/developers)
and:

```bash
cp .env.example .env
# edit .env — paste your key after OPENTOPOGRAPHY_API_KEY=
```

Without the key the land renders as flat buff; the OSM-derived
coastline and everything else is unaffected. Free-tier quota: 50
calls / 24 h, but each AOI's terrain tile is cached after the first
fetch.

## Programmatic API

For ad-hoc one-shot scripts:

```python
from render import coastal_chart
from drawables import ActiveSonar

coastal_chart(
    name="my_aoi", center=(lat, lon), half_w_km=8.0,
    drawables=[ActiveSonar(center=(lat, lon), range_m=900, label="DDS")],
    title="My port",
)
```

For repeated renders against the same frame (Preview + Save, batch
exports), use the session directly — fetch once, render many:

```python
from session import AOISession

s = AOISession(name="my_aoi", center=(lat, lon), half_w_km=8.0)
s.fetch()                            # ~3-5 s cold, parallel I/O + reproject
s.render(drawables=[], title="preview")        # ~0.4 s
s.render(drawables=all_sensors, title="final") # ~0.4 s
```

## Layout

Flat — everything at the repo root.

| File | What |
|---|---|
| `session.py` | `AOISession`: frame + fetched data, render-ready |
| `render.py` | matplotlib renderer + `ChartStyle` + `coastal_chart()` |
| `drawables.py` | sensor archetypes + annotations + measurements |
| `presets.py` | vendor preset catalog |
| `fetch.py` | bathy + OSMData land + SRTM + OSM features |
| `designer.py` | ipyleaflet UI |
| `designer.ipynb` | launcher |
| `cache/`, `images/`, `.env` | gitignored local state |

See [CLAUDE.md](./CLAUDE.md) for architectural detail.
