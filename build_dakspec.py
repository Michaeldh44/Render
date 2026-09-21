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
from shapely.geometry import mapping, Point
from shapely.ops import unary_union

SIMPLIFY_M = 0.10          # vereenvoudig ringen voor de tekening (10 cm)

OBJ_LABEL = {"lichtstraat": "lichtstraat", "lichtkoepel": "lichtkoepel",
             "schoorsteen": "schoorsteen", "dakdoorvoer": "dakdoorvoer",
             "installatie": "installatie", "dakraam": "dakraam", "overig": "overig"}

BRONNEN = ("Hoogte/3D: 3D BAG (TU Delft, CC BY 4.0) \u00b7 BAG/AHN/Luchtfoto: PDOK \u00b7 "
           "AHN-subtegels: GeoTiles (CC BY 4.0) \u00b7 dakscan 0.1-poc \u00b7 snapshot {snap} \u00b7 "
           "indicatief tenzij status validated \u00b7 maatklassen A (\u226410 cm) / B (~15 cm) / "
           "C (opstek/opgave)")


def _ring_to_local(coords, minx, maxy):
    """RD -> lokaal frame: x = lengte (O-W), y omlaag op de pagina, noord boven."""
    return [[round(x - minx, 2), round(maxy - y, 2)] for (x, y) in coords]


def _largest_polygon(geom):
    if geom is None or geom.is_empty:
        return None
    parts = [g for g in getattr(geom, "geoms", [geom]) if g.geom_type == "Polygon"]
    return max(parts, key=lambda p: p.area) if parts else None


def polys_from_vision(vision_dakvlakken):
    """Vision-dakvlakken -> shapely rechthoeken (RD) uit hun 4 hoeken."""
    from shapely.geometry import Polygon
    out = []
    for d in vision_dakvlakken or []:
        pts = d.get("poly_rd")
        if pts and len(pts) >= 3:
            try:
                p = Polygon(pts).buffer(0)
                if p.area > 0:
                    out.append(p)
            except Exception:
                pass
    return out


def split_dakvlakken(footprint, rects, min_area=5.0):
    """Snij Vision-deelvlakken uit het pand. Geeft [hoofdvlak, uitbouw, ...]
    (maatvaste polygonen), of [footprint] als er niks bruikbaars is."""
    fa = footprint.area
    exts, claimed = [], None
    for rect in sorted(rects, key=lambda r: r.area):        # klein eerst
        inter = _largest_polygon(footprint.intersection(rect))
        if inter is None or inter.area < min_area or inter.area > 0.6 * fa:
            continue
        if claimed is not None:
            inter = _largest_polygon(inter.difference(claimed))
        if inter and inter.area >= min_area:
            exts.append(inter)
            claimed = inter if claimed is None else claimed.union(inter)
    if not exts:
        return [footprint]
    main = _largest_polygon(footprint.difference(claimed)) or footprint
    return [main] + exts


def build(footprints, enrich=None, meta=None, opbouw=None, panddata=None, objecten=None, vision_status=None, dakvlakken_expliciet=None):
    """
    footprints : list[shapely Polygon] in RD (meters)
    dakvlakken_expliciet : expliciete deelvlak-polygonen (uit Vision-splitsing);
                 overschrijft de standaard pand-splitsing.
    enrich     : dict van dak_eigenschappen() (mag leeg)
    panddata   : dict van footprints_for_address() (bouwjaar, status)
    objecten   : list uit objecten.analyse() (Vision), elk met rd + type + zekerheid
    meta       : {ref, adres, regel, aantal_vhe, aantal_daken}
    """
    enrich = enrich or {}
    panddata = panddata or {}
    meta = meta or {}
    objecten = objecten or []
    if dakvlakken_expliciet:
        parts = [p.buffer(0) for p in dakvlakken_expliciet]
        union = unary_union(parts)
    else:
        union = unary_union([f.buffer(0) for f in footprints])   # clean + merge
        parts = list(getattr(union, "geoms", [union]))

    area_m2  = round(sum(p.area for p in parts), 2)           # ECHTE oppervlak (A)
    perim_m1 = round(sum(g.exterior.length for g in getattr(union, "geoms", [union])), 2)

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

    # --- objecten (Vision) -> genummerde onderdelen in lokaal frame ---
    onderdelen, objecten_lijst = [], []
    for nr, o in enumerate(objecten, start=1):
        rd = o.get("rd")
        gm = o.get("grootte_m") or (0.6, 0.6)
        item = {"nr": nr, "type": o.get("type", "overig"),
                "omschrijving": o.get("omschrijving", ""),
                "zekerheid": o.get("zekerheid", "laag")}
        if rd:
            lx, ly = round(rd[0]-minx, 2), round(maxy-rd[1], 2)
            onderdelen.append({"nr": nr, "type": item["type"], "label": item["type"],
                               "positie": [lx, ly], "grootte": list(gm),
                               "status": "gemeten" if o.get("zekerheid") == "hoog" else "opgave"})
        objecten_lijst.append(item)

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
    if panddata.get("bouwjaar"):
        dakgegevens.append({"label": "Bouwjaar (BAG)", "waarde": str(panddata["bouwjaar"]),
                            "eenheid": "", "maatklasse": "A"})
    if dh is not None:
        dakgegevens.append({"label": "Dakhoogte (boven maaiveld, 3D BAG)",
                            "waarde": f"{dh:.2f}", "eenheid": "m", "maatklasse": "B"})
    else:
        dakgegevens.append({"label": "Dakhoogte (boven maaiveld)",
                            "waarde": "onbekend (3D BAG niet beschikbaar)", "eenheid": ""})
    # opstand: hoogste en laagste dakrand uit 3D BAG hoogteverdeling
    oh, ol = enrich.get("opstand_hoog_mm"), enrich.get("opstand_laag_mm")
    if oh:
        dakgegevens.append({"label": "Opstand / dakrand \u2014 hoogste (3D BAG)",
                            "waarde": f"~ {oh}", "eenheid": "mm", "maatklasse": "B"})
    if ol is not None:
        dakgegevens.append({"label": "Opstand / dakrand \u2014 laagste (3D BAG)",
                            "waarde": f"~ {ol}", "eenheid": "mm", "maatklasse": "B"})
    if not oh and ol is None:
        dakgegevens.append({"label": "Opstand / dakrand (hoogte)",
                            "waarde": "n.t.b. (3D BAG niet beschikbaar; typisch 150\u2013300 mm)",
                            "eenheid": "", "maatklasse": "C"})
    dakgegevens += [
        {"label": "Afschot", "waarde": "n.t.b. (AHN-koppeling volgt)",
         "eenheid": "", "maatklasse": "C"},
    ]
    if objecten_lijst:
        from collections import Counter
        telling = Counter(o["type"] for o in objecten_lijst)
        samenvatting = ", ".join(f"{n}x {OBJ_LABEL.get(t, t)}" for t, n in telling.items())
        dakgegevens.append({"label": "Objecten op dak (Vision)", "waarde": samenvatting,
                            "eenheid": "", "maatklasse": "C"})
    else:
        vs = vision_status or "uit"
        dakgegevens.append({"label": "Objecten op dak (Vision)",
                            "waarde": f"geen herkend \u00b7 status: {vs}", "eenheid": ""})

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
            "onderdelen": onderdelen,
            "opstand": {"toon": True, "hoogte_mm": enrich.get("opstand_hoog_mm")},
        },
        "objecten_lijst": objecten_lijst if objecten_lijst else None,
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


def bouw_paginas(footprints, enrich=None, panddata=None, meta=None, objecten=None,
                 vision_status=None, dakvlakken=None):
    """OVERZICHT + één pagina per dakvlak. `dakvlakken` = expliciete deelvlak-
    polygonen (uit Vision-splitsing); zonder dat valt hij terug op de panden.
    Geeft list van {spec, footprint, objecten, label, dakvlakken}."""
    from shapely.geometry import Point
    objecten = objecten or []
    union = unary_union([f.buffer(0) for f in footprints])
    polys = dakvlakken if dakvlakken else list(getattr(union, "geoms", [union]))
    gelabeld = [(chr(65 + i), p) for i, p in enumerate(polys)]

    ov = build(footprints, enrich=enrich, panddata=panddata, meta=meta,
               objecten=objecten, vision_status=vision_status,
               dakvlakken_expliciet=(polys if dakvlakken else None))
    ov["pagina_label"] = "OVERZICHT"
    paginas = [{"spec": ov, "footprint": union, "objecten": objecten,
                "dakvlakken": gelabeld, "label": "A"}]

    if len(polys) > 1:
        for L, poly in gelabeld:
            obj_i = [o for o in objecten
                     if o.get("rd") and poly.buffer(0.5).contains(Point(*o["rd"]))]
            sp = build([poly], enrich=enrich, panddata=panddata, meta=meta,
                       objecten=obj_i, vision_status=vision_status)
            sp["pagina_label"] = f"DAKVLAK {L}"
            sp["meta"]["regel"] = f"Dakvlak {L} \u00b7 {sp['oppervlakte']['totaal_m2']:.0f} m\u00b2"
            paginas.append({"spec": sp, "footprint": poly, "objecten": obj_i,
                            "dakvlakken": None, "label": L})
    return paginas
