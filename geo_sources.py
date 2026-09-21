#!/usr/bin/env python3
"""
geo_sources.py  —  DE INVOER-LAAG (draait in JOUW omgeving)
===========================================================
Haalt uit open data op:
  1. adres            -> BAG pand-id(s)      via PDOK Locatieserver
  2. pand-id          -> footprint-polygoon  via PDOK BAG WFS  (RD, meters)
  3. pand-id          -> dakhoogte + daktype via 3D BAG API   (TU Delft)

Allemaal KEYLESS: geen API-sleutel nodig op dit pad.

LET OP — dit deel kon ik niet live testen (mijn sandbox mag PDOK niet
bereiken). De endpoints en veldnamen staan daarom als CONSTANTEN
bovenaan en de responses worden defensief uitgelezen. Draai dit bij jou,
en als een veldnaam net anders heet: alleen de constante/keylijst
aanpassen, de rest blijft staan.
"""
import requests
from shapely.geometry import shape

# ---- endpoints (pas hier aan als PDOK iets hernoemt) --------------------
LOCATIESERVER = "https://api.pdok.nl/bzk/locatieserver/search/v3_1"
BAG_WFS       = "https://service.pdok.nl/lv/bag/wfs/v2_0"
DRIEDBAG_API  = "https://api.3dbag.nl/collections/pand/items"   # OGC API Features
RD            = "EPSG:28992"     # Rijksdriehoek — coördinaten in meters
TIMEOUT       = 30
HEADERS       = {"User-Agent": "zaanstad-dakscan/0.1 (interne tool)"}

# 3D BAG veldnamen verschillen per versie; we proberen meerdere kandidaten.
K_H_DAK   = ["b3_h_dak_50p", "h_dak_50p", "b3_h_dak_70p"]
K_H_MAAI  = ["b3_h_maaiveld", "h_maaiveld"]
K_DAKTYPE = ["b3_dak_type", "dak_type"]
K_OPP_PLAT   = ["b3_opp_dak_plat", "opp_dak_plat"]
K_OPP_SCHUIN = ["b3_opp_dak_schuin", "opp_dak_schuin"]


def _first(props, keys):
    for k in keys:
        if k in props and props[k] not in (None, ""):
            return props[k]
    return None


# ---- 1. adres -> pand-id(s) ---------------------------------------------
def geocode_to_pandids(adres):
    """Geef (pandids:list[str], weergavenaam:str, centroide_rd:(x,y))."""
    r = requests.get(f"{LOCATIESERVER}/free",
                     params={"q": adres, "fq": "type:adres", "rows": 1},
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    docs = r.json().get("response", {}).get("docs", [])
    if not docs:
        raise LookupError(f"Geen adres gevonden voor: {adres!r}")
    doc = docs[0]

    # volledige velden ophalen (pandid zit in de lookup, niet in 'free')
    look = requests.get(f"{LOCATIESERVER}/lookup",
                        params={"id": doc["id"], "fl": "*"},
                        headers=HEADERS, timeout=TIMEOUT)
    look.raise_for_status()
    full = look.json().get("response", {}).get("docs", [{}])[0]

    pandids = full.get("pandid") or doc.get("pandid") or []
    if isinstance(pandids, str):
        pandids = [pandids]
    weergavenaam = full.get("weergavenaam") or doc.get("weergavenaam") or adres

    rd = None
    wkt = full.get("centroide_rd") or doc.get("centroide_rd")   # 'POINT(x y)'
    if wkt and wkt.startswith("POINT"):
        x, y = wkt[wkt.find("(")+1:wkt.find(")")].split()
        rd = (float(x), float(y))

    if not pandids:
        raise LookupError(f"Geen pand-id bij adres: {adres!r} (doc {doc.get('id')})")
    return pandids, weergavenaam, rd


# ---- 2. pand-id -> footprint (shapely polygon, RD/meters) ---------------
def pand_footprint(pandid):
    """GeoJSON pand-geometrie uit de BAG WFS, als shapely-polygoon in RD."""
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "bag:pand", "outputFormat": "application/json",
        "srsName": f"urn:ogc:def:crs:EPSG::28992", "count": 5,
        "CQL_FILTER": f"identificatie='{pandid}'",
    }
    r = requests.get(BAG_WFS, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    feats = r.json().get("features", [])
    if not feats:
        raise LookupError(f"Geen BAG-pand-geometrie voor id {pandid}")
    # meestal 1 feature; neem de grootste voor de zekerheid
    polys = [shape(f["geometry"]) for f in feats]
    polys.sort(key=lambda p: p.area, reverse=True)
    return polys[0]


# ---- 3. pand-id -> dak-eigenschappen (3D BAG) ---------------------------
def dak_eigenschappen(pandid):
    """Hoogtes + daktype uit 3D BAG. Faalt zacht -> geeft {} terug."""
    for ident in (pandid, f"NL.IMBAG.Pand.{pandid}"):
        try:
            r = requests.get(f"{DRIEDBAG_API}/{ident}",
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            props = r.json().get("properties", r.json())
            # 3D BAG nest soms per 'objecten'; pak de eerste dict met b3_-velden
            if not any(k.startswith(("b3_", "h_dak", "dak_type")) for k in props):
                for v in props.values():
                    if isinstance(v, dict) and any(str(k).startswith("b3_") for k in v):
                        props = v
                        break
            h_dak  = _first(props, K_H_DAK)
            h_maai = _first(props, K_H_MAAI)
            return {
                "h_dak_nap":   h_dak,
                "h_maaiveld_nap": h_maai,
                "dakhoogte_m": (round(h_dak - h_maai, 2)
                                if h_dak is not None and h_maai is not None else None),
                "dak_type":    _first(props, K_DAKTYPE),
                "opp_plat":    _first(props, K_OPP_PLAT),
                "opp_schuin":  _first(props, K_OPP_SCHUIN),
            }
        except requests.RequestException:
            continue
    return {}   # 3D BAG optioneel: footprint alleen is ook bruikbaar
