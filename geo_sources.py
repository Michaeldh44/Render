#!/usr/bin/env python3
"""
geo_sources.py  —  DE INVOER-LAAG (draait in JOUW omgeving)  [v0.2]
===================================================================
adres -> footprint(s) + dak-eigenschappen, uit open data (keyless):
  1. PDOK Locatieserver : adres -> coordinaat (RD) + evt. pand-id(s)
  2. PDOK BAG WFS       : pand-polygoon  (op id, of op kaartpositie)
  3. 3D BAG (TU Delft)  : dakhoogte + daktype

v0.2: het pand wordt bij voorkeur op de KAARTPOSITIE opgezocht
(punt-in-vlak). Dat werkt ook als het 'pandid'-veld in de Locatieserver-
response ontbreekt -- de oorzaak van de eerdere 404.
"""
import requests
from shapely.geometry import shape, Point

LOCATIESERVER = "https://api.pdok.nl/bzk/locatieserver/search/v3_1"
BAG_WFS       = "https://service.pdok.nl/lv/bag/wfs/v2_0"
DRIEDBAG_API  = "https://api.3dbag.nl/collections/pand/items"
TIMEOUT = 30
HEADERS = {"User-Agent": "zaanstad-dakscan/0.2 (interne tool)"}

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


def geocode(adres):
    """Geef dict: {naam, rd:(x,y), pandids:[...], velden:[...]}."""
    r = requests.get(f"{LOCATIESERVER}/free",
                     params={"q": adres, "fq": "type:adres", "rows": 1},
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    docs = r.json().get("response", {}).get("docs", [])
    if not docs:
        raise LookupError(f"Geen adres gevonden voor: {adres!r}")
    doc = docs[0]

    look = requests.get(f"{LOCATIESERVER}/lookup",
                        params={"id": doc["id"], "fl": "*"},
                        headers=HEADERS, timeout=TIMEOUT)
    full = (look.json().get("response", {}).get("docs", [{}])[0]
            if look.ok else {})

    src = {**doc, **full}
    rd = None
    wkt = src.get("centroide_rd")
    if wkt and wkt.startswith("POINT"):
        x, y = wkt[wkt.find("(")+1:wkt.find(")")].split()
        rd = (float(x), float(y))

    pandids = src.get("pandid") or []
    if isinstance(pandids, str):
        pandids = [pandids]

    return {"naam": src.get("weergavenaam", adres), "rd": rd,
            "pandids": pandids, "velden": sorted(src.keys())}


def _wfs(params):
    p = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
         "typeNames": "bag:pand", "outputFormat": "application/json",
         "srsName": "EPSG:28992", "count": 25, **params}
    r = requests.get(BAG_WFS, params=p, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("features", [])


def pand_by_id(pandid):
    feats = _wfs({"CQL_FILTER": f"identificatie='{pandid}'"})
    if not feats:
        raise LookupError(f"Geen BAG-pand voor id {pandid}")
    feats.sort(key=lambda f: shape(f["geometry"]).area, reverse=True)
    return feats[0]


def pand_at(x, y, box=2.0):
    """Pand op kaartpositie: kleine bbox rond het punt, kies het vlak dat
    het punt bevat (anders het dichtstbijzijnde)."""
    feats = _wfs({"BBOX": f"{x-box},{y-box},{x+box},{y+box},EPSG:28992"})
    if not feats:
        raise LookupError(f"Geen BAG-pand op positie ({x:.1f}, {y:.1f})")
    pt = Point(x, y)
    contains = [f for f in feats if shape(f["geometry"]).contains(pt)]
    pick = (contains or sorted(feats,
            key=lambda f: shape(f["geometry"]).distance(pt)))[0]
    return pick


def footprints_for_address(adres):
    """Geef (footprints:list[Polygon], naam:str, pandids:list[str])."""
    g = geocode(adres)
    footprints, pandids = [], []

    if g["pandids"]:                       # pad A: via pand-id
        for pid in g["pandids"]:
            feat = pand_by_id(pid)
            footprints.append(shape(feat["geometry"]))
            pandids.append(pid)
    elif g["rd"]:                          # pad B: op kaartpositie
        feat = pand_at(*g["rd"])
        footprints.append(shape(feat["geometry"]))
        pid = (feat.get("properties") or {}).get("identificatie")
        if pid:
            pandids.append(pid)
    else:
        raise LookupError(
            f"Geen pand te vinden voor {adres!r}. Beschikbare velden uit "
            f"Locatieserver: {g['velden']}")

    return footprints, g["naam"], pandids


def dak_eigenschappen(pandid):
    for ident in (pandid, f"NL.IMBAG.Pand.{pandid}"):
        try:
            r = requests.get(f"{DRIEDBAG_API}/{ident}",
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            props = r.json().get("properties", r.json())
            if not any(str(k).startswith(("b3_", "h_dak", "dak_type")) for k in props):
                for v in props.values():
                    if isinstance(v, dict) and any(str(k).startswith("b3_") for k in v):
                        props = v
                        break
            h_dak, h_maai = _first(props, K_H_DAK), _first(props, K_H_MAAI)
            return {
                "h_dak_nap": h_dak, "h_maaiveld_nap": h_maai,
                "dakhoogte_m": (round(h_dak - h_maai, 2)
                                if h_dak is not None and h_maai is not None else None),
                "dak_type": _first(props, K_DAKTYPE),
                "opp_plat": _first(props, K_OPP_PLAT),
                "opp_schuin": _first(props, K_OPP_SCHUIN),
            }
        except requests.RequestException:
            continue
    return {}
