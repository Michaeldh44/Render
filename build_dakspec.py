#!/usr/bin/env python3
"""
build_dakspec.py  —  HET CONTRACT-DEEL (geen netwerk, volledig testbaar)
========================================================================
footprint-polygoon/-polygonen (RD, meters) + dak-enrichment
        ->  dakspec-JSON die render.py leest.

Maatklasse-logica (de betrouwbaarheidslaag):
  A  = geometrie uit de BAG (oppervlak, omtrek)     -> hard
  B  = hoogte/daktype uit 3D BAG                     -> vrij hard
  C  = afschot / opstand / obstakels (nog niet gemeten of AHN-nog-niet-gekoppeld)
Verdict blijft NEEDS_REVIEW tot een inmeting valideert.
"""
from datetime import datetime, timezone
from shapely.geometry import mapping
from shapely.ops import unary_union

SIMPLIFY_M = 0.10          # vereenvoudig ringen voor de tekening (10 cm)

BRONNEN = ("Hoogte/3D: 3D BAG (TU Delft, CC BY 4.0) \u00b7 BAG/AHN/Luchtfoto: PDOK \u00b7 "
           "AHN-subtegels: GeoTiles (CC BY 4.0) \u00b7 dakscan 0.1-poc \u00b7 snapshot {snap} \u00b7 "
           "indicatief tenzij status validated \u00b7 maatklassen A (\u226410 cm) / B (~15 cm) / "
           "C (opstek/opgave)")


def _ring_to_local(coords, minx, maxy):
    """RD -> lokaal frame: x = lengte (O-W), y omlaag op de pagina, noord boven."""
    return [[round(x - minx, 2), round(maxy - y, 2)] for (x, y) in coords]


def build(footprints, enrich=None, meta=None, opbouw=None):
    """
    footprints : list[shapely Polygon] in RD (meters)
    enrich     : dict van dak_eigenschappen() (mag leeg)
    meta       : {ref, adres, regel, aantal_vhe, aantal_daken}
    """
    enrich = enrich or {}
    meta = meta or {}
    union = unary_union([f.buffer(0) for f in footprints])   # clean + merge
    parts = list(getattr(union, "geoms", [union]))

    area_m2  = round(sum(p.area for p in parts), 2)           # ECHTE oppervlak (A)
    perim_m1 = round(sum(p.exterior.length for p in parts), 2)  # omtrek dakrand (A)

    minx, miny, maxx, maxy = union.bounds
    lengte = round(maxx - minx, 2)      # horizontaal (O-W)
    breedte = round(maxy - miny, 2)     # verticaal (N-Z)

    dakvlakken = []
    for i, p in enumerate(parts):
        ring = list(p.simplify(SIMPLIFY_M, preserve_topology=True).exterior.coords)[:-1]
        dakvlakken.append({
            "id": chr(65 + i), "label": chr(65 + i),
            "punten": _ring_to_local(ring, minx, maxy),
            "oppervlak_m2": round(p.area, 2),
        })

    # --- hoogte / daktype (3D BAG) ---
    dh = enrich.get("dakhoogte_m")
    dak_type = (enrich.get("dak_type") or "").lower()
    is_plat = ("horizontal" in dak_type or "plat" in dak_type
               or (enrich.get("opp_plat") or 0) >= (enrich.get("opp_schuin") or 0))

    snap = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    dakgegevens = [
        {"label": "Omtrek dakrand (rondom)", "waarde": f"{perim_m1:.2f}",
         "eenheid": "m\u00b9", "maatklasse": "A"},
        {"label": "Aantal dakvlakken (panden in blok)", "waarde": str(len(parts)),
         "eenheid": "st"},
    ]
    if dh is not None:
        dakgegevens.append({"label": "Dakhoogte (boven maaiveld, 3D BAG)",
                            "waarde": f"{dh:.2f}", "eenheid": "m", "maatklasse": "B"})
    else:
        dakgegevens.append({"label": "Dakhoogte (boven maaiveld)",
                            "waarde": "onbekend (3D BAG niet beschikbaar)", "eenheid": ""})
    dakgegevens += [
        {"label": "Afschot", "waarde": "n.t.b. (AHN-koppeling volgt)",
         "eenheid": "", "maatklasse": "C"},
        {"label": "Opstand / dakrand (hoogte)", "waarde": "n.t.b. (opgave/inmeting)",
         "eenheid": "", "maatklasse": "C"},
        {"label": "HWA-punten / koepels / doorvoeren", "waarde": "n.t.b. (opgave)",
         "eenheid": ""},
    ]

    opbouw = opbouw or {
        "kop": "OPBOUW (advies \u2014 nog niet gemeten)",
        "rijen": [
            {"label": "Dakbedekking (advies)", "waarde": "APP gemod. bitumen, 2-laags"},
            {"label": "Isolatie", "waarde": "PIR 120 mm (Rc \u2248 5.4 m\u00b2K/W)"},
            {"label": "Dampremmer", "waarde": "bitumen/PE dampremmer"},
            {"label": "Randafwerking", "waarde": "aluminium daktrim + opstandstrook"},
            {"label": "Bron / zekerheid", "waarde": "advies o.b.v. BAG-geometrie; opbouw niet gemeten"},
        ],
    }

    return {
        "daktype": "plat" if is_plat else "hellend",
        "meta": {
            "ref": meta.get("ref", "zd-poc"),
            "titel": meta.get("adres", "onbekend adres"),
            "regel": meta.get("regel", f"{len(parts)} pand(en) = 1 dak"),
            "datum": datetime.now().strftime("%Y-%m-%d"),
            "verdict": "NEEDS_REVIEW",
        },
        "omhullende": {"breedte_m": breedte, "lengte_m": lengte},
        "geometrie": {
            "schaal": "auto op A3 (420 x 297 mm)",
            "noord_boven": True,
            "dakvlakken": dakvlakken,
            "onderdelen": [],
            "opstand": {"toon": True, "hoogte_mm": None},
        },
        "dakgegevens": dakgegevens,
        "oppervlakte": {
            "per_dakvlak": [{"label": f"Dakvlak {dv['label']} (plat)" if is_plat
                             else f"Dakvlak {dv['label']}", "m2": dv["oppervlak_m2"]}
                            for dv in dakvlakken],
            "totaal_m2": area_m2,
        },
        "materiaal": opbouw,
        "dakvisual": {
            "type": "placeholder",
            "onderschrift": ("DAKVISUAL \u2014 luchtfoto-uitsnede (PDOK) van het meetmodel "
                             "hierboven; geen maatbron: meet in het DXF, niet in dit beeld."),
            "let_op": "LET OP: voorcalculatie uit open data \u2014 inmeting op locatie blijft leidend.",
        },
        "bronnen": BRONNEN.format(snap=snap),
    }
