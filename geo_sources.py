#!/usr/bin/env python3
"""
geo_sources.py  —  DE INVOER-LAAG (draait in JOUW omgeving)  [v0.3]
===================================================================
adres -> footprint(s) + dak-eigenschappen, uit open data (keyless):
  1. PDOK Locatieserver : adres -> coordinaat (RD) + evt. pand-id(s)
  2. PDOK BAG WFS       : pand-polygoon + bouwjaar (op id of kaartpositie)
  3. 3D BAG (TU Delft)  : dakhoogte, daktype, hoogte-percentielen -> opstand

v0.3: 3D BAG-response is een CityJSONFeature; de b3_-attributen zitten
onder CityObjects[..].attributes. Daaruit ook een opstand-AANNAME
(hoogste dakpunten t.o.v. dakvlak-mediaan).
"""
import requests
from shapely.geometry import shape, Point

LOCATIESERVER = "https://api.pdok.nl/bzk/locatieserver/search/v3_1"
BAG_WFS       = "https://service.pdok.nl/lv/bag/wfs/v2_0"
DRIEDBAG_API  = "https://api.3dbag.nl/collections/pand/items"
TIMEOUT = 30
HEADERS = {"User-Agent": "zaanstad-dakscan/0.3 (interne tool)"}

K_H_DAK50 = ["b3_h_dak_50p", "h_dak_50p"]
K_H_DAK70 = ["b3_h_dak_70p", "h_dak_70p"]
K_H_DAKMAX = ["b3_h_dak_max", "h_dak_max"]
K_H_DAKMIN = ["b3_h_dak_min", "h_dak_min"]
K_H_MAAI  = ["b3_h_maaiveld", "h_maaiveld"]
K_DAKTYPE = ["b3_dak_type", "dak_type"]
K_OPP_PLAT   = ["b3_opp_dak_plat", "opp_dak_plat"]
K_OPP_SCHUIN = ["b3_opp_dak_schuin", "opp_dak_schuin"]
K_OPP_GROND  = ["b3_opp_grond", "opp_grond"]
K_H_NOK      = ["b3_h_nok", "h_nok"]


def _first(props, keys):
    for k in keys:
        if k in props and props[k] not in (None, ""):
            return props[k]
    return None


def geocode(adres):
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
    feats = _wfs({"BBOX": f"{x-box},{y-box},{x+box},{y+box},EPSG:28992"})
    if not feats:
        raise LookupError(f"Geen BAG-pand op positie ({x:.1f}, {y:.1f})")
    pt = Point(x, y)
    contains = [f for f in feats if shape(f["geometry"]).contains(pt)]
    pick = (contains or sorted(feats,
            key=lambda f: shape(f["geometry"]).distance(pt)))[0]
    return pick


def _panddata(props):
    props = props or {}
    return {"bouwjaar": props.get("bouwjaar") or props.get("oorspronkelijkbouwjaar"),
            "status": props.get("status")}


def footprints_for_address(adres):
    """Geef (footprints:list[Polygon], naam, pandids:list, panddata:dict)."""
    g = geocode(adres)
    footprints, pandids, panddata = [], [], {}
    if g["pandids"]:
        for i, pid in enumerate(g["pandids"]):
            feat = pand_by_id(pid)
            footprints.append(shape(feat["geometry"]))
            pandids.append(pid)
            if i == 0:
                panddata = _panddata(feat.get("properties"))
    elif g["rd"]:
        feat = pand_at(*g["rd"])
        footprints.append(shape(feat["geometry"]))
        pid = (feat.get("properties") or {}).get("identificatie")
        if pid:
            pandids.append(pid)
        panddata = _panddata(feat.get("properties"))
    else:
        raise LookupError(
            f"Geen pand te vinden voor {adres!r}. Velden: {g['velden']}")
    return footprints, g["naam"], pandids, panddata


def dak_eigenschappen(pandid):
    """Hoogtes/daktype + opstand-AANNAME uit 3D BAG (CityJSONFeature)."""
    for ident in (f"NL.IMBAG.Pand.{pandid}", pandid):
        try:
            r = requests.get(f"{DRIEDBAG_API}/{ident}",
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            data = r.json()
            attrs = data.get("properties") or {}
            # 3D BAG item: CityJSONFeature zit onder data["feature"]
            cj = data.get("feature", data)
            for obj in (cj.get("CityObjects") or {}).values():
                a = obj.get("attributes", {}) if isinstance(obj, dict) else {}
                if any(str(k).startswith("b3_") for k in a):
                    attrs = a
                    break
            if not attrs:
                continue
            h50 = _first(attrs, K_H_DAK50)
            h70 = _first(attrs, K_H_DAK70)
            hmin = _first(attrs, K_H_DAKMIN)
            hmax = _first(attrs, K_H_DAKMAX)
            hmaai = _first(attrs, K_H_MAAI)
            dakhoogte = round(h50 - hmaai, 2) if h50 is not None and hmaai is not None else None
            # opstand = dakrand boven het dakvlak. Uit de hoogteverdeling:
            #   hoog  = hoogste dakpunt   boven mediaan dakvlak
            #   laag  = hoogste dakpunt   boven 70-percentiel (hoger dakdeel)
            def _mm(a, b):
                return round((a - b) * 1000) if a is not None and b is not None else None
            opstand_hoog = _mm(hmax, h50)
            opstand_laag = _mm(hmax, h70)
            opp_plat = _first(attrs, K_OPP_PLAT)
            opp_schuin = _first(attrs, K_OPP_SCHUIN)
            opp_grond = _first(attrs, K_OPP_GROND)
            # classificatie plat/hellend uit de dakoppervlakken
            pl, sh = opp_plat or 0, opp_schuin or 0
            is_plat = sh <= 0.2 * (pl + sh) if (pl + sh) else True
            # gemiddelde hellingshoek (indicatief) uit projectie schuin dak
            import math as _m
            helling_deg = None
            if not is_plat and opp_schuin and opp_grond:
                ratio = min(1.0, max(0.0, (opp_grond - pl) / opp_schuin)) if opp_schuin else 0
                if 0 < ratio <= 1:
                    helling_deg = round(_m.degrees(_m.acos(ratio)), 1)
            return {
                "h_dak50_nap": h50, "h_dak70_nap": h70,
                "h_dakmax_nap": hmax, "h_dakmin_nap": hmin, "h_maaiveld_nap": hmaai,
                "h_nok_nap": _first(attrs, K_H_NOK),
                "dakhoogte_m": dakhoogte,
                "opstand_hoog_mm": opstand_hoog,
                "opstand_laag_mm": opstand_laag,
                "dak_type": _first(attrs, K_DAKTYPE),
                "opp_plat": opp_plat, "opp_schuin": opp_schuin, "opp_grond": opp_grond,
                "is_plat": is_plat, "helling_deg": helling_deg,
            }
        except requests.RequestException:
            continue
    return {}
