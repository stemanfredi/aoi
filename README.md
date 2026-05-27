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

### Alternative: Streamlit web app

Same engine, different shell — a browser app instead of a Jupyter
notebook. Useful for sharing a stable URL or running headless.

```bash
.venv/bin/streamlit run app.py
```

Opens `localhost:8501`. Sidebar holds the controls (title, aspect,
layer toggles, sensors, measurements, Preview / Save / Download);
main area is the Leaflet map + the rendered preview image. Sensor
placement is click-to-add on the map (the notebook's drag-to-move
markers don't translate to folium; lat/lon is edited in the sidebar
form instead).

**Cloud deploy** ([Streamlit Community Cloud](https://share.streamlit.io)):
point a new app at `app.py`, and in Secrets set
`AOI_SIMPLIFIED_LAND = "1"` — the full land dataset (~1.3 GB) won't fit
the free tier's disk, so the engine falls back to the ~12 MB simplified
coastline (coarser, but it boots). Optionally add `OPENTOPOGRAPHY_API_KEY`
for terrain.

> **Deployed apps are public by default** — anyone with the URL can open
> the app, render, and download PNGs, and every visitor shares your
> single instance and your OpenTopography quota. To restrict access, set
> a viewer allowlist under the app's **Settings → Sharing** (a private
> GitHub repo hides the *code*, but the running app stays URL-reachable
> until you set the allowlist).

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
| `designer.py` | ipyleaflet UI (Jupyter notebook path) |
| `designer.ipynb` | notebook launcher |
| `app.py` | Streamlit UI (web app path) |
| `cache/`, `images/`, `.env` | gitignored local state |

See [CLAUDE.md](./CLAUDE.md) for architectural detail.
