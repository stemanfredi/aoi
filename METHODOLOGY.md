# Methodology — siting underwater sensors on a coastal AOI

A practitioner playbook for using this engine to produce defensible
sensor-siting charts. Grounded in cited literature and current
manufacturer datasheets. Where this doc disagrees with marketing copy,
follow the doc.

The engine itself (`chart.py`, `sensors.py`, `presets.py`) is
deliberately a **geometric instrument** — it draws what you tell it to
draw on a bathymetric + terrain base. The reasoning behind those
geometric choices is what this document captures.


## 1. What this document is

A reasoning aid for three audiences:

1. **A practitioner siting sensors against a real coastline** — read top
   to bottom; §2 is the recipe, §4-§9 explain why each step is shaped
   the way it is.
2. **A reader comparing vendor options** — §11 is the honest
   capability matrix, cross-referenced to independent validation rather
   than vendor copy.
3. **A future maintainer of this engine** — §6 documents what each
   archetype models and §12 documents what it deliberately doesn't.

Everything numeric in this document is sourced. Where a number is
practitioner folklore with no peer-reviewed anchor, it is labelled
*[heuristic]* — those are the places to push back hardest before
betting a design on them.


## 2. The design loop

Seven steps. Use them in order. They map directly onto the engine's
public surface (`designer.ipynb` for steps 2-6, `coastal_chart()` for
step 6, this doc for steps 1, 3 and 7).

1. **State the question.** What asset is being protected, what threat
   class drives the design, what response posture is assumed? Without
   these three the rest is decoration. "Protect FPSO turret from
   open-circuit divers, 5-minute RHIB intercept" is a design question;
   "make a map of the bay" is not.
2. **Frame the AOI.** Open `designer.ipynb`, pick an aspect ratio, pan
   and zoom until the chart frames the asset *and* the doctrinal outer
   ring (§4). If you can't see both, the frame is wrong.
3. **Sketch the doctrinal rings.** Add `ZonePolygon` annotations for
   the Detect / Classify / Interdict rings. Their radii are derived in
   §4 from threat speed and response time, *not* from any sensor's
   datasheet.
4. **Drop the long-range layer first.** Passive arrays
   (`PassiveArray`) go on baselines further offshore — they own the
   outer Detect ring (§9). Place them as the threat axis demands, not
   centred on the asset.
5. **Then the close-in active layer.** Active sonars (`ActiveSonar`)
   go at chokepoints, sector-backed against walls where structure
   helps you (§8). Their coverage circles overlap each other by
   ~20-30% in vendor convention; this overlap is *not* peer-reviewed
   *[heuristic]*.
6. **Annotate threats and constraints.** `TextLabel` for named assets
   and incidents; `ZonePolygon` for exclusion / traffic-separation /
   no-go.
7. **Render and sanity-check.** Run `coastal_chart()` (or click Preview
   then Save in `designer.ipynb`). The engine produces one publication-grade look —
   full-bleed nautical line-art with OSM coastline, IHO isobaths, SRTM
   land contours, and sensor overlays. Then walk back through §7's
   derating factors: do the drawn ranges still pass once you apply
   summer-stratification and bottom-type penalties to the headline
   numbers?


## 3. The siting problem

Underwater sensor coverage is a function of three things the chart can
draw and four things it can't.

**Drawable (the chart engine handles these):**
- AOI bathymetry (EMODnet / GEBCO) and OSM coastline — the marine
  substrate.
- SRTM 30 m land terrain — contours and labels on the land side, so
  the strait geometry, ridgelines, and approaches read at a glance.
- Sensor archetype geometry (circle / wedge / Voronoi / track buffer).
- Doctrinal zones (annotation polygons).

**Not drawable from the chart alone (you carry these in your head):**
- Sound-speed profile and its seasonal cycle.
- Bottom type and the reverberation it produces.
- Ambient noise (shipping, wind, biologics).
- Frequency-dependent absorption.

The chart is therefore an instrument for **layout reasoning** — does
this geometry plausibly cover this threat axis on this coastline — not
for **performance prediction**. Performance prediction is BELLHOP /
KRAKEN territory and is deliberately out of scope (§12). The two
disciplines are complementary; this document is about the first.


## 4. Layered defense doctrine

Underwater port protection is publicly described as a defense-in-depth
architecture organised around the **Detect → Classify → Interdict**
sequence [1, 2]. The IAEA physical-protection family uses the cognate
*Detect → Delay → Respond* triad [3]; the maritime literature
substitutes "Classify" for "Delay" because in water the limiting step
is identifying the contact, not slowing it.

The width of each ring is set by **response time × threat speed**, not
by what any sensor can see:

- A closed-circuit rebreather (CCR) diver at ~1 kt (0.5 m/s) over a
  5-minute intercept timeline needs the outer ring at ≥ 150 m.
- A swimmer delivery vehicle (SDV) at ~5 kt (2.6 m/s) over the same
  5-minute window needs the ring at ≥ 780 m.
- A 30-knot surface USV needs it at ≥ 4.7 km — and is usually not the
  underwater system's job.

This is the reasoning Molyboha and Zabarankin formalise as a
worst-case stochastic optimisation [4], and it is why the chart must
frame the **outer ring**, not the **sensor footprint**.

Standoff and asset-protection numbers below the outer ring are usually
facility-specific or classified — public NATO products at the
unclassified level (STANAG 1364 is restricted; ANEP-77 is a different
domain) do not publish specific waterside distances.

**The chart's job in the doctrinal frame:**

| Layer | What the chart shows | Sensor archetype |
|---|---|---|
| Detect (outer) | Doctrinal ring + passive nodes on baselines | `PassiveArray` |
| Classify (middle) | Active coverage circles/wedges at chokepoints | `ActiveSonar` |
| Interdict (inner) | Physical barriers, response staging | `ZonePolygon`, `TextLabel` (engine has no `BarrierBoom` yet — §12) |


## 5. Threat archetypes

Every range claim in §7 and §11 is meaningless without a stated
target. The table below is the threat catalog the rest of this
document indexes against.

| Threat | Typical speed | TS (dB re 1 m², active) | SL (dB re 1 µPa @ 1 m, passive) | Notes |
|---|---|---|---|---|
| Open-circuit diver (OCD) | 0.5-1 kt fin; 1-2 kt with DPV | -10 to -15 [5] | 80-110 broadband, peak 2-12 kHz [6] | Bubbles dominate active and passive |
| Closed-circuit diver (CCD/CCR) | 0.5-1 kt | -15 to -25 [5] | Near-silent [7] | Hardest target — quieter active and passive |
| Diver Propulsion Vehicle + diver | 1.2-2.5 kt | Body TS + small motor whine | Faint motor tonals | Active + passive cue |
| Swimmer Delivery Vehicle (wet) | 5-9 kt | +5 to +10 *[heuristic]* | Motor + hull flow noise | Long-range active is the lead sensor |
| Dry Combat Submersible | ~5 kt cruise | Submarine-scale | Pressurised hull, quieter than wet SDV | Long-range active + passive line array |
| Small UUV/AUV (Autosub class) | 2-4 kt | -10 to -20 *[heuristic]* | ~124 dB at 100 Hz-5 kHz [8] | Designed for low signature |
| Surface USV | 5-30 kt | Surface return | Surface noise | Radar + EO; outside DDS regime |
| Midget submarine | 4-8 kt submerged | Submarine-scale | Pressurised hull | Towed array / hull-mounted ASW territory |
| Merchant ship | 10-25 kt | Hull-scale | 170-190 (188 measured on 54 kGT container) [9] | Detectable at tens of km in deep water |

The 90-dB gap between merchant-ship SL (~180) and diver SL (~90) is
the single most important fact in §7: a passive system claiming "30 km
vessel detection" and a passive system claiming "300 m diver
detection" can be the **same** sensor.


## 6. Sensor archetypes — what the engine models and what it omits

Four drawable sensor primitives, plus two annotation primitives. Each
is a deliberate stylisation; the stylisation choices are listed
explicitly below so future-you knows what corner you cut.

### `ActiveSonar` — circle or sector wedge
- **Models:** the range at which the active monostatic sonar equation
  yields signal excess > 0 against a stated target, in the vendor's
  reference environment.
- **Omits:** acoustic shadow behind piers/breakwaters/moored hulls;
  frequency-dependent absorption variation across the AOI; SSP
  refraction; reverberation regime.
- **Honesty rule:** the drawn radius is meaningful only against the
  preset's `notes` target class in the vendor's reference conditions.
  Apply §7 derating before betting a design on it.

### `PassiveNode` — single pod + transparent range circle
- **Models:** a single hydrophone pod with a stylised
  **single-pod sensitivity envelope** against a stated target class
  (vessel ≈ 10 km, quiet sub ≈ 3 km, diver/UUV ≈ 0.5-2 km — see §5).
  Dashed circle edge mirrors the MIL-STD-2525D "anticipated/planned"
  convention (§10).
- **Omits:** array gain (would apply if the pod is itself a small
  cluster), bearing-accuracy ellipses, target-class scaling (a single
  circle can only honour one threat class at a time), TL refraction.
- **Honesty rule:** the circle is **a planning aid, not a measured
  footprint**. The same physical pod against a vessel and against a
  diver gives radii two orders of magnitude apart. Pick the design
  threat class and document it in the label.
- **Design use:** place pods one at a time; the **s = r rule of thumb**
  (§8) — next pod at the edge of the previous circle — gives
  cross-bearing geometry by construction.

### `PassiveArray` — N nodes + Voronoi tessellation
- **Models:** spatial sampling density across an already-laid-out
  multi-node array. Each Voronoi cell is the locus of points closer to
  that node than to any other.
- **Omits:** array gain (AG ≈ 10 log N for ideal isotropic noise [10]),
  bearing-accuracy ellipses, grating-lobe ambiguities, depth-dependent
  propagation.
- **Honesty rule:** a passive node is a **bearing instrument**, not a
  range instrument. The Voronoi cell is a layout-density cue, not a
  detection footprint. Detection bands are bearing-dependent ellipses
  that depend on the target's source level — properties no single
  radius captures.
- **`PassiveNode` vs `PassiveArray`:** use `PassiveNode` for
  free-form one-at-a-time sketching (any pod-with-circle convention,
  including the s = r spacing pattern). Use `PassiveArray` for an
  already-defined multi-node layout where Voronoi cells communicate
  the spatial-responsibility partition.

### `SurveySwath` — track buffered to half-swath
- **Models:** the across-track coverage of a multibeam echosounder
  along a survey line, at a swath ratio appropriate to the band
  (typically 3-5 × water depth at 200 kHz, up to 7 × in shallow water
  per the NORBIT WINGHEAD i80S Trondheim case study [11]).
- **Omits:** outer-beam quality degradation, refraction artefacts,
  cross-line overlap planning to IHO S-44 [12].

### `TextLabel`, `ZonePolygon` — annotation primitives
- `TextLabel` for named assets, incidents, navigation notes.
- `ZonePolygon` for doctrinal rings, exclusion zones, traffic
  separation. Three line styles (solid / dashed / dotted) carry
  semantic load — see §10.


## 7. Why headline ranges deceive

The active sonar equation, plain-language [13, 14]:

```
SE = SL − 2·TL + TS − (NL − DI) − DT            (noise-limited)
SE = SL −   TL + TS − RL          − DT          (reverb-limited)
```

The passive equation drops TS and the factor of 2 on TL [13]:

```
SE = SL − TL − (NL − DI) − DT
```

Vendors publish a single "detection range" per target class — e.g.
"1000 m against open-circuit divers" — by setting SE = 0 in *their*
reference environment. That environment is implicitly: isothermal
water, low ambient noise, ideal bottom, low Pfa tolerance. None of
those hold in a working port.

**Four factors derate the headline.** Each has a peer-reviewed anchor;
the rules of thumb beneath are practitioner heuristics that should be
sense-checked against a real TL model before final siting:

1. **Frequency-driven absorption** [15, 16]. ~20-25 dB/km round-trip
   at 70 kHz; ~100 dB/km at 400 kHz. This is why NORBIT GuardPoint 400
   (150 m OCD) and GuardPoint 70 (800 m OCD) sit at opposite ends of
   the same product family.
2. **Reverberation, not noise, limits at port scale** [17, 14]. RL
   falls slower with range than signal does, so simply raising SL does
   not extend range. Doppler-discriminating waveforms (CutFM,
   broadband chirps) do.
3. **Summer downward-refracting profiles** [18] create a near-surface
   shadow zone. A diver at 1-3 m may be invisible to a sensor mounted
   at 5 m even at half the headline range. Treat the headline as
   winter/isothermal best case. *[heuristic: subtract 20-40% in
   summer-stratified ports]*
4. **Target strength scatter** [5]. OCD TS varies ±10 dB depending on
   aspect, gear, depth, and breath phase. CCR is ~10 dB quieter again.
   A 10 dB TS drop is roughly a 40% range loss in spherical spreading
   and ~70% in cylindrical.

**The Forcys disclosure as a benchmark.** Forcys is the only vendor
that publishes a typical-vs-peak gap explicitly: ~600 m typical
European waters, up to 1000 m in good propagation [19]. Treat 60-70%
of any vendor's headline as a defensible operational planning radius;
the chart engine can draw the headline as a dashed envelope and the
derated number as a solid inner ring (the latter requires the
`range_typical_m` field — §12 roadmap).


## 8. Geometric placement rules

The rules below follow naturally from §7 physics + §5 threat geometry.

- **DDS at chokepoints, passives on baselines.** A single 360° DDS
  head sweeps ~1-2.5 km²; placed across a 500-m harbour mouth one
  head covers the whole entrance [1]. Passive arrays, being bearing
  instruments (§6), want **maximum aperture across the threat axis**
  — typical baseline 0.5-2 km across the outer approach.
- **Sector heads back to quay walls.** No point ensonifying concrete.
  Forcys, NORBIT and DSIT all sell sectoral variants for this reason
  [19, 20]. The chart engine encodes this with `beam_width_deg < 360`
  + `bearing_deg`.
- **Acoustic shadow behind piers, breakwaters, moored vessels** is
  the dominant constraint in < 30 m water [4]. Line-of-sight is more
  binding than headline range. A second offset head is needed to look
  "behind" every solid structure.
- **Bathymetry funnels threats.** Channels and sills concentrate
  ingress paths; deep-draft assets must transit the dredged channel,
  so DDS along the channel axis dominates expected-trajectory
  coverage [4].
- **DDS install depth 2-30 m** [19]. Above 2 m the head sees surface
  clutter; below 30 m the shallow-water duct may bend rays into the
  seabed. Optimum depth follows from the local SSP.
- **Coverage overlap 20-30%** is widely quoted vendor convention but
  has no peer-reviewed source [1, 19, 20]. *[heuristic — verify]*

**Passive-pod spacing — the s = r family of design patterns.** When
sketching with single-pod range circles (`PassiveNode`), the spacing
between adjacent pods carries the design intent. Three patterns,
parametrised by the ratio of spacing `s` to single-pod radius `r`:

| Design intent | Spacing | What you get |
|---|---|---|
| Barrier detection only ("did something cross?") | s ≤ 2 r | Adjacent circles just touch — no gap along the axis, but bearing-only fixes |
| **Cross-bearing localisation ("where is it now?")** | **s ≤ r** | **Every point covered by ≥ 2 pods — true 2D fixes; the practical default** |
| Tight triangulation ("continuous low-σ track") | s ≤ r · √3 / 2 ≈ 0.87 r | Equilateral overlap — sub-degree fixes throughout the strip |

The **s = r rule** ("place the next pod at the edge of the previous
pod's circle") is the practical default — it produces cross-bearing
coverage by construction while remaining visually legible on the
chart. It is target-class-invariant: the radius scales with the design
threat (§5), but the geometric rule is the same. *[heuristic — derived
from circle-overlap geometry; cross-bearing accuracy further depends
on aperture, SNR, and bearing-only Cramér-Rao bounds]*


## 9. Multi-sensor combination logic

The publicly documented cueing pattern is **passive detects → active
classifies → response acts**, marketed by Forcys/Wavefront as SInAPS
(Simultaneous In-band Active and Passive Sonar) [19, 21].

- **Passive-only** is preferred for stealth (no active emissions
  betray sensor position) and against low-TS CCR divers whose
  breathing modulation is detectable but whose echo is weak [22, 23].
- **Active-only** is preferred in low-noise scenes (CCR, small UUV)
  where passive has nothing to listen to, and where ID-by-echo-
  structure is needed.
- **Both, time-multiplexed or in-band:** the SInAPS pitch is that one
  head provides 360° passive monitoring while the active beam
  classifies a cued bearing. Endurance drives this — active sonar
  consumes power and ensonifies the environment continually [19].
- **Cueing latency limits the geometry.** An SDV at 5 kt covers 150 m
  in one minute; at chokepoints the system is left in continuous
  active mode regardless.

The chart cannot show cueing — but the chart's *layout* implies it.
Passive nodes on the outer baseline + active heads at chokepoints +
overlap between the two is the visual signature of a SInAPS-style
design.


## 10. Cartographic conventions

The engine's symbology choices align with three sources:

- **IHO INT1** (paper-chart symbology) and **NOAA U.S. Chart No. 1**
  [24, 25] reserve **magenta with T-dash limit** for regulated /
  restricted areas. The S-100 framework (specifically S-101 ENC RESARE
  features and S-122 Marine Protected Areas) carries the convention
  forward into ECDIS [26].
- **MIL-STD-2525D §5.3.5** [27] encodes **dashed line = anticipated /
  planned**, **solid = present**. The engine's `ActiveSonar` dashed
  edge therefore correctly signals a designed footprint, not a
  validated one.
- **Thyng et al. 2016** [28] established `cmocean.deep` as a
  perceptually uniform, partially colorblind-safe bathymetric ramp —
  cited here as the reference colour ramp for bathymetric work even
  though the current engine renders a single line-art look (no fill
  colormap on the sea side). Land terrain elevation labels follow the
  same two-channel discipline.

**Two-channel rule.** Every semantic distinction is carried by at
least two visual channels — hue + line style + marker — so charts
survive monochrome photocopying and red-green colour-vision deficiency
(~8% of male readers) [29].

| Sensor | Hue | Edge | Marker |
|---|---|---|---|
| `ActiveSonar` | red | dashed | star |
| `PassiveNode` | blue | dashed | filled dot |
| `PassiveArray` | blue | solid Voronoi | filled dot |
| `SurveySwath` | teal | solid centerline + buffer | — |
| `ZonePolygon` `kind="restricted"` *(roadmap)* | magenta | T-dash | — |

**Caption metadata minimums** (currently rendered as a single-line
caption — see §12 roadmap for a structured block): title, AOI name,
horizontal datum (WGS 84), vertical datum (LAT or MSL as drawn),
projection (UTM zone N), scale ratio, bathymetry source + year,
coastline source + retrieval date, production date, sensor inventory.
Add a classification banner if the chart will carry one.


## 11. Vendor capability matrix

Numbers below are reconciled against current vendor datasheets and
trade-press as of May 2026. Where vendor copy differs from an
independent measurement, the independent number wins; where validation
is press-release tier (no public quantitative report), it is labelled so.

### Active diver-detection sonars

| Model | f (kHz) | Coverage | OCD | CCD | SDV / mini-sub | UUV | Source level | Install depth | Validation tier | Sources |
|---|---|---|---|---|---|---|---|---|---|---|
| Forcys Sentinel 2 IDS Permanent | 70 | 360° | 1000 m peak / ~600 m typical Euro | not in current copy | 1500 m | up to 1500 m | 206 dB re 1 µPa @ 1 m | 2-30 m | Sonardyne ANTX (press-release tier) | [19] |
| Forcys Sentinel 2 IDS Expeditionary | 70 | 360° | 1000 m | — | 1500 m | up to 1500 m | 206 dB | trailer-mountable | first customer delivery May 2025 | [19] |
| NORBIT GuardPoint 70 | 70 | 360° (sectoral option) | 800 m | 400 m | — | > 1000 m | — | — | NSWC Panama City Division, Dec 2024 trial under 5-yr CRADA extension (press-release tier) | [20] |
| NORBIT GuardPoint 100 | 100 | 180° | 600 m | 300 m | — | 750 m | — | ≤ 100 m | — | [20] |
| NORBIT GuardPoint 200 | 200 | 180° | 400 m | 200 m | — | > 600 m | — | — | — | [20] |
| NORBIT GuardPoint 400 | 400 | 180° | 150 m | 80 m | — | 200 m | — | 1-2 m | Danube, Hungarian MoD | [20] |

The Sentinel's 0.35° bearing accuracy and 1 m position accuracy at
150 m range [19] are the kind of secondary specs that should constrain
how tight you draw the inner-ring annotations.

### Passive systems

| System | Architecture | Band | Headline reach | Honest qualifier | Sources |
|---|---|---|---|---|---|
| Image Soft IS UNWAS (Gen 3, UDT 2023 launch) | Cabled piezoelectric, SOSUS-architecture, COTS-GPU shore station, deep-learning classifier | broadband | > 30 km against subs and surface vessels in good conditions | "Good conditions" = isothermal shallow channel à la Gulf of Finland; not credible against modern quiet subs without further qualification | [30, 31] |
| Optics11 OptiBarrier (launched 22 May 2025) | All-optical fibre Fabry-Perot, no wet-end electronics, single interrogator services 100 km of cable | 10 Hz - 10 kHz | "Up to 150 km" (Paul Heiden, CEO) — but vendor's own zone scheme is Zone 1 ≤ 6 km real-time tracking, Zone 2 ≤ 30 km broad-spectrum detection, Zone 3 > 100 km | The 150 km figure is a far-zone unprocessed-detection ceiling on loud targets, not a tracking range. Plan to Zone 2 (≤ 30 km) for operational design | [32, 33] |
| Optics11 OptiArray (towed) | All-optical fibre towed array | submarine SONAR bands | RNLN Orka-class selection via Naval Group + Thales (NEDS 21 Nov 2024 Rotterdam; Thales sonar suite Mar 2025) | **Distinct product from OptiBarrier** — towed, not seabed-curtain | [34, 35] |

The Optics11 / Image Soft "long passive reach" claims are reconcilable
only if you fix the target SL: ~180 dB merchant gives ~30+ km in good
conditions; ~90 dB diver gives a couple of hundred metres at best
(§5). Treat headline-reach as a *vessel-class* number.

### Survey sonars (engine completeness)

| Model | f (kHz) | Swath / depth ratio | Sources |
|---|---|---|---|
| NORBIT WINGHEAD i80S | 200 (variable) | 3-5 × depth typical at 200 kHz, up to ~7 × in shallow water (210° swath geometry; > 2 × at 300 m depth per NORBIT Trondheim case study) | [11] |


## 12. What the engine deliberately does NOT model — roadmap

These are flagged not as bugs but as *premature* relative to the
geometric-instrument scope of the current engine. Address them when a
specific chart genuinely needs them — not before.

**Acoustic physics layer (deferred — §7's derating is currently in
this document, not in code):**
- Francois-Garrison frequency-dependent absorption [15] keyed to the
  AOI's cached bathy NetCDF temperature/salinity.
- Coupling to a TL solver (BELLHOP, KRAKEN) for per-pixel signal-
  excess maps instead of range circles.
- Sound-speed profile import (WOA / GLORYS) with seasonal mode.
- Bottom-type derate keyed to admiralty / EMODnet seabed substrate.
- Bearing-accuracy ellipses for `PassiveArray` nodes (Cramér-Rao
  bound, plane-wave).

**Structured preset metadata (deferred — currently in `notes` prose
inside `presets.py`):**
- `ranges_by_target` dict per `ActiveSonar` preset (OCD / CCD / SDV /
  UUV) replacing the single `range_m`.
- `range_typical_m` alongside `range_m` for two-ring rendering.
- `frequency_khz`, `source_level_db`, `install_depth_m_min/max` as
  first-class fields.
- `baseline_type` ∈ {linear, curtain, T, L, distributed},
  `design_freq_hz`, `typical_target` on `PassiveArray`.
- `validation` field structuring NSWC / CRADA / sea-trial citations
  with `(program, year, location, public_report)` tuples.
- `zone_role` ∈ {detect, classify, interdict} on sensors.

**Engine archetypes and rendering (deferred):**
- `BarrierBoom` primitive in `sensors.py` for physical anti-swimmer
  nets / port-security barriers — currently no way to draw the inner
  Interdict layer's hardware.
- `ZonePolygon` `kind="restricted"` rendering with INT1 magenta
  T-dash limit + S-101 RESARE alignment.
- Classification banner in `ChartStyle` (CAPCO conventions [36]).
- `PassiveArray` node glyph (e.g. "P") for B&W disambiguation;
  `SurveySwath` solid centerline for the same reason.
- `caveat_box` annotation primitive that surfaces preset caveats as a
  footnote when any sensor's `notes` flags a marketing-vs-evidence
  gap.

**Recently graduated from the roadmap** (implemented in the engine):
- Auto-generated sensor inventory legend block — `show_sensor_legend`
  in `ChartStyle`, drawn by `_sensor_legend()` in `chart.py`.
- `PassiveNode` archetype with dashed-edge transparent range circle
  (the s = r design pattern is now expressible directly in the chart).

When any of these blocks a real chart, promote the relevant item from
this list to an actual implementation task. Until then, leaving them
deferred keeps the engine honest about what it does.


## References

[1] Kessel, R. T. & Hollett, R. D. (2006). *Underwater Intruder
    Detection Sonar for Harbour Protection: State of the Art Review
    and Implications.* NURC-PR-2006-027. https://openlibrary.cmre.nato.int/handle/20.500.12489/609

[2] US Navy. *NTTP 3-10.1 Naval Coastal Warfare* (2005).

[3] IAEA. *Handbook on the Design of Physical Protection Systems*
    (Detect-Delay-Respond model). https://www.iaea.org/publications/13459/

[4] Molyboha, A. & Zabarankin, M. (2012). *Stochastic Optimization of
    Sensor Placement for Diver Detection.* Operations Research 60(2)
    292-312. https://pubsonline.informs.org/doi/10.1287/opre.1110.1032

[5] Hollett, R., Kessel, R. T. & Pinto, M. (2006). *At-Sea
    Measurements of Diver Target Strength.* NURC-PR-2006-002, DTIC
    ADA454750. https://apps.dtic.mil/sti/tr/pdf/ADA454750.pdf

[6] *Underwater Noises of Open-Circuit Scuba Diver,* Archives of
    Acoustics 2020. https://journals.pan.pl/Content/116325/PDF/aoa.2020.133155.pdf

[7] Lennartsson et al. (2012). *Passive acoustic detection of
    closed-circuit underwater breathing apparatus in an operational
    port environment.* JASA 132(4) EL310. https://pubs.aip.org/asa/jasa/article/132/4/EL310/830963

[8] Griffiths et al. (2001). *On the radiated noise of the Autosub
    AUV.* ICES Journal of Marine Science 58(6) 1195. https://academic.oup.com/icesjms/article/58/6/1195/641551

[9] McKenna et al. *Underwater radiated noise from modern commercial
    ships.* PMC 5612799. https://pmc.ncbi.nlm.nih.gov/articles/PMC5612799/

[10] Urick, R. J. (1983). *Principles of Underwater Sound,* 3rd ed.
    McGraw-Hill. Ch. 2 (sonar equations), Ch. 3 (array gain).

[11] NORBIT. *Introducing the WINGHEAD i80S long-range multibeam
    sonar* (Trondheim Fjord case study). https://norbit.com/case-study/introducing-the-norbit-winghead-i80s-long-range-multibeam-sonar

[12] IHO. *S-44 Standards for Hydrographic Surveys.* Edition 6.1.0,
    2022. https://iho.int/en/standards-and-specifications

[13] Urick, R. J. (1983). *Principles of Underwater Sound,* 3rd ed.
    McGraw-Hill, Ch. 2.

[14] Ainslie, M. A. (2010). *Principles of Sonar Performance
    Modelling.* Springer. Ch. 3 (sonar equations), Ch. 8
    (reverberation).

[15] Francois, R. E. & Garrison, G. R. (1982). *Sound absorption
    based on ocean measurements.* Implemented at NPL Technical Guide.
    http://resource.npl.co.uk/acoustics/techguides/seaabsorption/

[16] *Field measurements of acoustic absorption 38-360 kHz,* JASA
    148(1) 100 (2020). https://pubs.aip.org/asa/jasa/article/148/1/100/962878

[17] Hjelmervik, K. & Sletner, P. (2011). *The impact of reverberation
    on active sonar optimum frequency.* Proceedings of Meetings on
    Acoustics 12, 070001. https://pubs.aip.org/asa/poma/article/12/1/070001/995176

[18] SACLANTCEN CP-42. *Environmental impact on mobile sonar shallow
    water operations.* https://apps.dtic.mil/sti/pdfs/AD1114257.pdf

[19] Forcys / Wavefront Systems. Sentinel IDS product page +
    Permanent Protection datasheet.
    https://www.forcys.com/instruments/sentinel-ids/ ;
    https://www.wavefront.systems/sentinel-faqs/

[20] NORBIT. GuardPoint 70 / 100 / 200 / 400 product pages.
    https://norbit.com/explore-our-solutions/products/

[21] Wavefront Systems. *SInAPS — the game-changer for IDS systems.*
    https://www.wavefront.systems/sinaps-the-game-changer-for-ids-systems/

[22] Lo Iacono, A. et al. *Experimental Results of Diver Detection in
    Harbor Environments Using Single Acoustic Vector Sensor.*
    Archives of Acoustics. https://acoustics.ippt.pan.pl/index.php/aa/article/view/4182

[23] Zhang et al. *Diver Detection Sonars and Target Strength: Review
    and Discussions.* ICSV14. https://www.acoustics.asn.au/conference_proceedings/ICSV14/papers/p221.pdf

[24] IHO. *INT1 — Symbols, Abbreviations, Terms used on charts.*
    https://iho.int/en/standards-and-specifications

[25] NOAA. *U.S. Chart No. 1.* https://nauticalcharts.noaa.gov/publications/us-chart-1.html

[26] IHO. *S-101 Electronic Navigational Chart Product Specification*
    and *S-122 Marine Protected Areas Product Specification.*
    https://iho.int/en/iho-s-101-to-s-199

[27] Joint Chiefs of Staff. *MIL-STD-2525D Joint Military Symbology,*
    2014. https://www.jcs.mil/Portals/36/Documents/Doctrine/Other_Pubs/ms_2525d.pdf

[28] Thyng, K. M. et al. (2016). *True Colors of Oceanography:
    Guidelines for Effective and Accurate Colormap Selection.*
    Oceanography 29(3). https://tos.org/oceanography/article/true-colors-of-oceanography-guidelines-for-effective-and-accurate-colormap

[29] Ordnance Survey. *Guide to Cartography — Colour.*
    https://docs.os.uk/more-than-maps/geographic-data-visualisation/guide-to-cartography/colour

[30] Image Soft. *IS UNWAS — 3rd Generation launch (UDT 2023,
    Rostock).* https://imagesoft.fi/3rd-gen-unwas-launch-next-level-underwater-surveillance/

[31] Janes (May 2023). *Finnish company launches new surveillance
    device to protect undersea infrastructure.* Tuomas Pöyry quote.

[32] Optics11. *OptiBarrier launch — immediate over-the-horizon
    threat detection (22 May 2025).* https://optics11.com/underwater-security/optibarrier-launch-immediate-over-the-horizon-threat-detection-24-7/

[33] Popular Science. *How listening to light waves could prevent
    subsea cable sabotage.* Paul Heiden, CEO, 150 km zone scheme.
    https://www.popsci.com/technology/light-waves-listening-subsea-cables-sabotage-optics11/

[34] Janes. *Optics11 signs agreement with Thales on fibre-optic
    towed array for RNLN Orka submarines* (NEDS 2024, 21 Nov 2024).

[35] Naval News (Mar 2025). *Thales and Optics11 Equip Dutch
    Submarines.* https://www.navalnews.com/naval-news/2025/03/thales-and-optics11-equip-dutch-submarines/

[36] DNI Office. *Authorized Classification and Control Markings
    Register* (CAPCO). https://www.dni.gov/files/documents/FOIA/Authorized%20Classification%20and%20Control%20Markings%20Register%20V1.2.pdf
