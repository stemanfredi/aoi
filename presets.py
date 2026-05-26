"""Sensor preset catalog — defaults for chart overlays.

Focused on four vendors of underwater coastal-protection sensors:

  Passive hydrophone systems:
    - Image Soft (FI) — IS UNWAS — cabled piezoelectric SOSUS-style array
    - Optics11 (NL) — OptiBarrier — all-optical fibre seabed curtain

  Diver Detection Sonars (active):
    - NORBIT (NO) — GuardPoint 70 / 100 / 200 / 400 (frequency-tiered family)
    - Forcys / Wavefront (UK) — Sentinel 2 IDS (with SInAPS®)

Each preset is a dict that can be passed via build_sensor() to produce one of
the dataclasses in _sensors.py. Numbers are manufacturer-declared figures
under stated optimal conditions — typical operational ranges in cluttered
or marginal conditions run at roughly 60-70% of the peak number (Forcys is
the only vendor that publishes both peak and typical: ~1000 m peak vs.
~600 m typical European waters for diver detection).

For active sonars the catalog uses the OPEN-CIRCUIT DIVER (OCD) detection
range as the primary `range_m` (the most common siting target). Per-target
ranges (CCD / SDV / UUV) are documented in `notes` for reference.

Sources: vendor product pages verified May 2026. For the design playbook
these presets are meant to support — layered-defense doctrine, range
derating, cartographic conventions — see METHODOLOGY.md.

To use:
    from presets import PRESETS, list_presets, build_sensor
    sensor = build_sensor("Forcys Sentinel 2 IDS",
                           center=(lat, lon))
"""
from typing import Any, Dict, List, Optional, Tuple

from drawables import ActiveSonar, PassiveArray, PassiveNode, SurveySwath


# ── Catalog ────────────────────────────────────────────────────────────────
# Keys are display names. Values include:
#   type            — one of "ActiveSonar", "PassiveArray", "SurveySwath"
#   range_m         — primary range used to draw coverage. For active: OCD
#                     diver detection range. For passive arrays: not used by
#                     the chart (coverage is geometry, not a radius).
#   beam_width_deg  — active only: 360 = omni, < 360 = sector
#   bearing_deg     — active only, required if beam_width_deg < 360
#   color           — fill/edge color
#   notes           — short human-readable summary including per-target ranges
#   source          — provenance citation (vendor URL)
PRESETS: Dict[str, Dict[str, Any]] = {

    # ───── GENERIC SKETCH PRIMITIVES — vendor-neutral ─────
    # Use these when sketching a layout without committing to a specific
    # vendor. Override range_m at place-time in designer.py.

    "Active sonar (generic)": dict(
        type="ActiveSonar",
        range_m=1000,                       # sketch default; override at place-time
        beam_width_deg=360,                 # omni; for a wedge, use a vendor sectoral preset
        color="#c0392b",
        notes=("Vendor-neutral active-sonar sketch primitive. Default 1000 m "
               "omnidirectional. Override range_m at place-time to match the "
               "design intent (typical diver-detection 0.6-1 km, SDV 1-1.5 km — "
               "see METHODOLOGY.md §7 for derating from vendor headlines)."),
        source="generic",
    ),

    "Passive node (generic)": dict(
        type="PassiveNode",
        range_m=2000,                       # sketch default; override at place-time
        color="#2980b9",
        notes=("Vendor-neutral passive single-pod sketch primitive. The circle "
               "is a stylized sensitivity envelope against a stated target "
               "class (see METHODOLOGY.md §6); use the s = r rule of thumb "
               "(next pod at the edge of this circle) for cross-bearing "
               "localization geometry (METHODOLOGY.md §8). Override range_m "
               "at place-time: ~10 km for vessel-class targets, ~3 km for "
               "quiet subs, 0.5-2 km for diver/UUV — see the threat table in "
               "METHODOLOGY.md §5."),
        source="generic",
    ),

    # ───── FORCYS / WAVEFRONT — Sentinel 2 IDS ─────
    "Forcys Sentinel 2 IDS (Permanent)": dict(
        type="ActiveSonar",
        range_m=1000,                       # OCD peak; ~600 m typical Euro waters
        beam_width_deg=360,
        color="#c0392b",
        notes=("70 kHz LPM, 20 kHz bandwidth, SL 206 dB re 1 µPa @ 1 m, "
               "360° acoustic cover, SInAPS® (simultaneous active+passive). "
               "Per-target peak: OCD 1000 m (typical ~600 m Euro waters), "
               "CCD ~700 m (legacy figure; not in current Forcys datasheet), "
               "SDV/mini-sub 1500 m, UUV/drone up to 1500 m (vendor headline). "
               "256 receive beams, 0.35° bearing accuracy, 1 m position accuracy "
               "at 150 m range. 45.5 kg stainless-steel head (432 × 330 mm), "
               "70 W max, install depth 2-30 m. Mesh network up to 10 sonars, "
               "open API for C2 integration. Most-deployed IDS globally since 2009."),
        source="https://www.forcys.com/instruments/sentinel-ids/",
    ),
    "Forcys Sentinel 2 IDS (Expeditionary)": dict(
        type="ActiveSonar",
        range_m=1000,                       # same head, same range
        beam_width_deg=360,
        color="#cb4335",
        notes=("Same sensor head as Permanent (70 kHz, 360°, SL 206 dB, SInAPS®, "
               "~1000 m OCD / 1500 m UUV/mini-sub) on a towed trailer "
               "(1959×2237×3368 mm, 1850 kg trailer weight; head spec identical "
               "to Permanent per current datasheet). Mobile Command and Control "
               "Unit for Expeditionary Deployment (MCCU-ED); 4 kW generator, "
               "65 L diesel, 80-hour autonomous power, 10 m telescopic comms "
               "mast, wireless mesh up to 10 sonars; 20-minute setup. First "
               "customer delivery May 2025."),
        source=("https://www.forcys.com/forcys-deliver-sentinel-ids-expeditionary-trailers"
                "-to-a-long-standing-defence-customer/"),
    ),

    # ───── NORBIT — GuardPoint family ─────
    "NORBIT GuardPoint 70": dict(
        type="ActiveSonar",
        range_m=800,                        # OCD; UUV >1000 m, surveillance "up to 1000 m"
        beam_width_deg=360,                 # sectoral transmission optionally available
        color="#a93226",
        notes=("70 kHz, 360° (sectoral option). "
               "Per-target: OCD 800 m, CCD 400 m, UUV >1000 m. "
               "Long-range portable. Sonar head <50 kg + <10 kg topside. "
               "Designed for open water, naval bases, ports, oil rigs/FPSO. "
               "Validated by US NSWC Panama City Division in a Dec 2024 trial "
               "under a 5-year CRADA extension (tested alongside GuardPoint 100; "
               "detected multiple AUV form factors)."),
        source="https://norbit.com/explore-our-solutions/products/guardpoint-70",
    ),
    "NORBIT GuardPoint 100 (180° sector)": dict(
        type="ActiveSonar",
        range_m=600,                        # ~750 m envelope per trade press; vendor doesn't headline a single figure
        beam_width_deg=180,
        bearing_deg=0,
        color="#cb4335",
        notes=("100 kHz, up to 180° horizontal, 20° vertical scan window with "
               "1.9° per-beam width (STX, electronic tilt, no moving parts). "
               "Per-target (Seismic Asia Pacific datasheet mirror, PS-250001-4): "
               "OCD 600 m+, CCD 300 m+, AUV/SDV/mini-sub 750 m+. "
               "Install depth ≤ 100 m. Designed for harbours with obstacles — "
               "the narrow vertical beam reduces seabed/surface multipath."),
        source="https://norbit.com/explore-our-solutions/products/guardpoint-100",
    ),
    "NORBIT GuardPoint 200 (180° sector)": dict(
        type="ActiveSonar",
        range_m=400,                        # OCD; UUV >600 m
        beam_width_deg=180,
        bearing_deg=0,
        color="#cd6155",
        notes=("200 kHz, up to 180° horizontal, STX narrow vertical beam. "
               "Per-target: OCD 400 m, CCD 200 m, UUV >600 m. "
               "Medium range up to ~600 m total envelope. "
               "Shallow water, harbours with obstacles."),
        source="https://norbit.com/explore-our-solutions/products/guardpoint-200",
    ),
    "NORBIT GuardPoint 400 (180° sector)": dict(
        type="ActiveSonar",
        range_m=150,                        # OCD; UUV 200 m, max envelope ~200 m
        beam_width_deg=180,
        bearing_deg=0,
        color="#d98880",
        notes=("400 kHz, up to 180° horizontal. "
               "Per-target: OCD 150 m, CCD 80 m, UUV 200 m. "
               "Ultra-shallow specialist (1-2 m water): rivers, dams, restricted "
               "areas, cruise/mega-yachts. Validated on the Danube with "
               "Hungarian MoD. Highly portable, single-person deploy "
               "(sonar head 2.9 kg in air)."),
        source="https://norbit.com/explore-our-solutions/products/guardpoint-400",
    ),

    # ───── IMAGE SOFT — IS UNWAS ─────
    "Image Soft IS UNWAS": dict(
        type="PassiveArray",
        nodes_default=6,
        spacing_m_default=2000,             # km-scale baselines typical
        color="#1f618d",
        notes=("Cabled piezoelectric seabed array, hybrid optical/power cable to "
               "shore + COTS GPU shore station. SOSUS-architecture, modernised. "
               "Three generations (Gen1 long-running with a European navy; "
               "Gen2 tropical-water harbour protection for an Asian MoD; "
               "Gen3 launched UDT 2023 with deep-learning classifier). "
               "Headline detection: >30 km against subs/surface vessels in good "
               "conditions (Tuomas Pöyry, VP, Janes, May 2023; plausible for "
               "surface vessels in the Gulf of Finland's isothermal shallow "
               "channel, not credible against modern quiet subs without further "
               "qualification). Validated in Gulf of Finland."),
        source=("https://imagesoft.fi/coastal-defence-systems/is-unwas/ + "
                "https://imagesoft.fi/3rd-gen-unwas-launch-next-level-underwater-surveillance/"),
    ),

    # ───── OPTICS11 — OptiBarrier ─────
    "Optics11 OptiBarrier (4-node trial layout)": dict(
        type="PassiveArray",
        nodes_default=4,                    # 4-node Optics11 sea trial layout (2019)
        spacing_m_default=400,
        color="#2980b9",
        notes=("All-optical fibre Fabry-Perot interferometric hydrophones. "
               "No electronics in the wet end (EMI-immune, no corrosion paths). "
               "Pods in 200 m / 600 m depth variants; 10 Hz - 10 kHz band; "
               "noise floor below Sea State 0. "
               "Single shore interrogator services 100 km of cable with no "
               "sensitivity loss. OptiBarrier launched 22 May 2025. "
               "Vendor zone scheme: Zone 1 ≤ 6 km (real-time tracking), "
               "Zone 2 ≤ 30 km (broad-spectrum detection), Zone 3 > 100 km "
               "(headline ceiling — 'up to 150 km vessel detection', "
               "Paul Heiden, CEO, Optics11 launch May 2025 — is a far-zone "
               "unprocessed detection ceiling on loud targets, not a tracking "
               "range; plan operationally to Zone 2). "
               "Hydrophones every ~1 km in operational deployments (4-node "
               "here is the 2019 Optics11 sea trial layout; TNO involvement "
               "appears in later trials). "
               "Note: Optics11's submarine towed array (OptiArray, selected for "
               "RNLN Orka via Naval Group + Thales, NEDS 21 Nov 2024 Rotterdam + "
               "Thales sonar suite Mar 2025) is a distinct product — towed, not "
               "a seabed curtain — and is not chartable as a fixed installation."),
        source=("https://optics11.com/underwater-security/optibarrier-launch-immediate"
                "-over-the-horizon-threat-detection-24-7/ + "
                "https://optics11.com/blog/successful-sea-trial-of-optics11-fiber-optic"
                "-hydrophone-array/"),
    ),
    "Optics11 OptiBarrier (operational ~1 km spacing)": dict(
        type="PassiveArray",
        nodes_default=8,                    # representative operational deployment
        spacing_m_default=1000,             # vendor-stated hydrophone spacing
        color="#3498db",
        notes=("Operational OptiBarrier layout — hydrophones every ~1 km, "
               "single shore interrogator, scales to hundreds of sensing points "
               "along a single fibre. Coverage is bearing-only per node (passive "
               "array — see METHODOLOGY.md §6 for why no honest single radius "
               "exists); cross-bearings between adjacent nodes give 2D fixes. "
               "Plan to Zone 2 (≤ 30 km vessel detection) for operational design; "
               "the km-scale 'per-node radius' that varies most across vendor "
               "copy conflates SL across target classes (vessel ~180 dB vs "
               "diver ~90 dB — see METHODOLOGY.md §5). "
               "Use this preset for representative production-style barriers; "
               "for the 2019 sea-trial layout use the 4-node trial preset."),
        source=("https://optics11.com/underwater-security/optibarrier-launch-immediate"
                "-over-the-horizon-threat-detection-24-7/"),
    ),

    # ───── Survey-swath placeholder (NORBIT WINGHEAD i80S) — engine completeness ─
    "NORBIT WINGHEAD i80S multibeam (200 kHz, swath ~3-5×depth)": dict(
        type="SurveySwath",
        swath_m_default=400,                # at ~80 m depth, swath ≈ 5 × depth → ~400 m;
                                            # caller should override to chart depth × ~5
        color="#16a085",
        notes=("Survey-time multibeam echosounder. Not a security sensor — "
               "included so SurveySwath has a worked preset. swath_m ≈ 3-5 × "
               "water depth at 200 kHz (up to ~7× in shallow water per the "
               "NORBIT WINGHEAD Trondheim Fjord case study; 210° angular swath, "
               ">2× depth coverage demonstrated at 300 m depth). Override to "
               "your chart's nominal depth."),
        source=("https://norbit.com/case-study/introducing-the-norbit-winghead"
                "-i80s-long-range-multibeam-sonar"),
    ),
}


# ── Helpers ────────────────────────────────────────────────────────────────

def list_presets(filter_type: Optional[str] = None) -> List[str]:
    """List preset display names, optionally filtered by sensor type."""
    if filter_type is None:
        return list(PRESETS.keys())
    return [k for k, v in PRESETS.items() if v["type"] == filter_type]


def build_sensor(preset_name: str, *,
                 center: Optional[Tuple[float, float]] = None,
                 nodes: Optional[list] = None,
                 track: Optional[list] = None,
                 label: Optional[str] = None,
                 **overrides: Any):
    """Instantiate the right sensor dataclass from a preset.

    `center`/`nodes`/`track` are required for the corresponding sensor types.
    `label` defaults to the preset display name; `overrides` win over defaults
    (e.g. range_m=600 to use the typical-European-waters figure for Sentinel).
    """
    if preset_name not in PRESETS:
        raise KeyError(f"unknown preset {preset_name!r} — try list_presets()")
    preset = PRESETS[preset_name].copy()
    sensor_type = preset.pop("type")
    preset.pop("notes", None)
    preset.pop("source", None)
    preset.update(overrides)
    label = label or preset_name

    if sensor_type == "ActiveSonar":
        if center is None:
            raise ValueError("ActiveSonar preset needs center=(lat, lon)")
        return ActiveSonar(
            center=center,
            range_m=preset["range_m"],
            label=label,
            bearing_deg=preset.get("bearing_deg"),
            beam_width_deg=preset.get("beam_width_deg", 360),
            color=preset.get("color"),
        )
    if sensor_type == "PassiveNode":
        if center is None:
            raise ValueError("PassiveNode preset needs center=(lat, lon)")
        return PassiveNode(
            center=center,
            range_m=preset["range_m"],
            label=label,
            target_class=preset.get("target_class"),
            color=preset.get("color"),
        )
    if sensor_type == "PassiveArray":
        if not nodes:
            raise ValueError("PassiveArray preset needs nodes=[(lat, lon), ...]")
        return PassiveArray(
            nodes=nodes,
            label=label,
            color=preset.get("color"),
        )
    if sensor_type == "SurveySwath":
        if not track:
            raise ValueError("SurveySwath preset needs track=[(lat, lon), ...]")
        return SurveySwath(
            track=track,
            swath_m=preset.get("swath_m", preset.get("swath_m_default", 200)),
            label=label,
            color=preset.get("color"),
        )
    raise ValueError(f"unknown preset type {sensor_type!r}")
