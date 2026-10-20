#!/usr/bin/env python3
"""
opstand.py  —  zichtbare opstanden (witte/lichte daktrim) en splitsing van dakvlakken
=====================================================================================
3D BAG kent alleen hoogteverschillen. Een dak dat even hoog doorloopt maar door
een opstand in twee velden is verdeeld (bv. een uitbouw met eigen witte daktrim)
blijft daar één dakvlak. En de witte trim zelf werd door de objectherkenning
als 'installatie' of 'lichtstraat' gezien.

Dit module:
  1. vind_lijnen()  — smalle, lange, lichte structuren op de luchtfoto
                      (white top-hat: lichter dan de omgeving en smaller dan 0,7 m).
  2. splits()       — splitst een plat dakvlak langs een opstandlijn als er
                      bewijs is over de hele breedte:
                        a) lijnstukken die beide randen van het dakvlak raken
                           (de trim is vaak alleen aan de uiteinden zichtbaar), of
                        b) de lijn is over >= 50% van de breedte zichtbaar, of
                        c) >= 15% zichtbaar én een hoogtesprong (AHN) >= 0,15 m.
  3. is_opstand()   — een gevonden object dat grotendeels op een opstandlijn
                      ligt die aan een rand vastzit, is geen object.

  verfijn(img, frame, footprint, dakvlakken, dsm, rings, buren)
      -> (dakvlakken, lijnen, info)
"""
import math
import numpy as np
import cv2
from shapely.geometry import Polygon, LineString, Point, MultiLineString
from shapely.ops import split as _split, unary_union

import segment2 as s2

TOPHAT_M = 1.4          # kernel: lichte structuren smaller dan dit
MIN_LIJN_M = 1.5
MAX_BREED_M = 1.0
MIN_SLANK = 4.0         # lengte/breedte
RAND_TOL_M = 1.0        # 'raakt de rand' binnen deze afstand
LANGS_RAND_M = 0.8      # lijn ligt langs de rand (daktrim op de gevel)
HOEK_TOL = 6.0
OFFSET_TOL_M = 0.6
MIN_DEEL_M2 = 8.0
MIN_DEEL_BREED_M = 2.0
MIN_SPLIT_VLAK_M2 = 30.0
AS_TOL = 8.0            # splitslijn moet evenwijdig of haaks op de hoofdas van het dakvlak staan
SPRONG_SPLIT_M = 0.15


def _hoek(p, q):
    return math.degrees(math.atan2(q[1]-p[1], q[0]-p[0])) % 180.0


def _hoekverschil(a, b):
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _kandidaat_masker(image_path, frame, footprint):
    img = cv2.imread(image_path)
    if img is None:
        return None
    H, W = img.shape[:2]
    mpp = s2._mpp(W, H, frame)
    L = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)[..., 0]
    zone = s2._poly_mask(footprint.buffer(0.5), W, H, frame)
    dak = s2._poly_mask(footprint, W, H, frame, erode_px=max(1, int(1.0/mpp)))
    if dak.sum() < 100:
        return None
    Lr = float(np.median(L[dak]))
    k = max(5, int(round(TOPHAT_M/mpp)) | 1)
    th = cv2.morphologyEx(L, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    kand = zone & (th > 30) & (L > Lr + max(35.0, 0.3*(255-Lr)))
    m = cv2.morphologyEx(kand.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return m, mpp, W, H


def _breedte_px(m, p, q, max_px):
    """Mediane breedte (px) van het masker loodrecht op p->q: aaneengesloten run
    door de hartlijn (lichte dingen ernaast tellen niet mee)."""
    H, W = m.shape
    d = np.array(q, float) - np.array(p, float); n = np.linalg.norm(d) or 1.0
    d /= n; nrm = np.array([-d[1], d[0]])

    def aan(c, s):
        x, y = (c + nrm*s).astype(int)
        return 0 <= x < W and 0 <= y < H and m[y, x]

    br = []
    for t in np.linspace(0.1, 0.9, 11):
        c = np.array(p, float) + d*n*t
        start = next((s for s in sorted(range(-3, 4), key=abs) if aan(c, s)), None)
        if start is None:
            continue
        a = start
        while a - 1 >= -max_px and aan(c, a - 1):
            a -= 1
        b = start
        while b + 1 <= max_px and aan(c, b + 1):
            b += 1
        br.append(b - a + 1)
    return float(np.median(br)) if len(br) >= 4 else 1e9


def vind_lijnen(image_path, frame, footprint):
    """Rechte, smalle, lichte lijnen (daktrim/opstand). Hough op het kandidaatmasker,
    zodat een L-vormige trim (rand + uitloper naar binnen) als twee lijnen telt.
    -> [{lijn: LineString RD, rect: Polygon RD, lengte, breedte, hoek}]"""
    r = _kandidaat_masker(image_path, frame, footprint)
    if r is None:
        return []
    m, mpp, W, H = r
    segs = cv2.HoughLinesP(m*255, 1, np.pi/360, threshold=max(20, int(1.0/mpp)),
                           minLineLength=int(MIN_LIJN_M/mpp), maxLineGap=int(0.3/mpp))
    if segs is None:
        return []
    # bijna-dubbele segmenten (dikke lijn -> meerdere Hough-lijnen) samenvoegen
    groepen = []
    for x1, y1, x2, y2 in np.asarray(segs).reshape(-1, 4):
        p, q = (float(x1), float(y1)), (float(x2), float(y2))
        h = _hoek(p, q)
        for g in groepen:
            if _hoekverschil(g["h"], h) > 5:
                continue
            gp, gq = g["p"], g["q"]
            d = np.array(gq) - np.array(gp); n = np.linalg.norm(d) or 1.0; d /= n
            nrm = np.array([-d[1], d[0]])
            off = max(abs(np.dot(np.array(p)-gp, nrm)), abs(np.dot(np.array(q)-gp, nrm)))
            if off*mpp > 0.5:
                continue
            ts = [0.0, n, float(np.dot(np.array(p)-gp, d)), float(np.dot(np.array(q)-gp, d))]
            if min(ts[2], ts[3]) > n + 0.5/mpp or max(ts[2], ts[3]) < -0.5/mpp:
                continue
            t0, t1 = min(ts), max(ts)
            g["p"] = tuple(np.array(gp) + d*t0); g["q"] = tuple(np.array(gp) + d*t1)
            break
        else:
            groepen.append({"p": p, "q": q, "h": h})
    uit = []
    max_px = int(1.0/mpp)
    for g in groepen:
        p, q = g["p"], g["q"]
        lang = math.dist(p, q)*mpp
        breed = _breedte_px(m, p, q, max_px)*mpp
        if lang < MIN_LIJN_M or breed > MAX_BREED_M or lang < MIN_SLANK*max(breed, 0.05):
            continue
        a = s2._rd_of(p[0], p[1], W, H, frame); b = s2._rd_of(q[0], q[1], W, H, frame)
        lijn = LineString([a, b])
        uit.append({"lijn": lijn, "rect": lijn.buffer(max(breed, 0.2)/2, cap_style=2),
                    "lengte": round(lijn.length, 2), "breedte": round(breed, 2), "hoek": _hoek(a, b)})
    return uit


def _langs_rand(lijn, poly):
    """Ligt de lijn (vrijwel geheel) langs de buitenrand van poly?"""
    rand = poly.exterior
    pts = [lijn.interpolate(t, normalized=True) for t in np.linspace(0, 1, 9)]
    return sum(rand.distance(p) <= LANGS_RAND_M for p in pts) >= 7


def _raakt_rand(lijn, poly):
    a, b = Point(lijn.coords[0]), Point(lijn.coords[-1])
    r = poly.exterior
    return r.distance(a) <= RAND_TOL_M or r.distance(b) <= RAND_TOL_M


def _doorsnijding(lijn, poly):
    """Verleng de lijn over het hele dakvlak en knip af op het dakvlak.
    -> het stuk dat de lijn bevat (LineString) of None."""
    (x0, y0), (x1, y1) = lijn.coords[0], lijn.coords[-1]
    d = math.hypot(x1-x0, y1-y0) or 1.0
    ux, uy = (x1-x0)/d, (y1-y0)/d
    R = math.hypot(poly.bounds[2]-poly.bounds[0], poly.bounds[3]-poly.bounds[1]) + 5
    mx, my = (x0+x1)/2, (y0+y1)/2
    lang = LineString([(mx-ux*R, my-uy*R), (mx+ux*R, my+uy*R)])
    s = lang.intersection(poly)
    if s.is_empty:
        return None
    stukken = list(getattr(s, "geoms", [s]))
    stukken = [g for g in stukken if g.geom_type == "LineString"]
    if not stukken:
        return None
    mid = Point(mx, my)
    return min(stukken, key=lambda g: g.distance(mid))


def _zijhoogte(dsm, lijn, poly, kant, van=0.6, tot=2.5):
    """Mediaan DSM in een strook naast de lijn (kant = +1/-1)."""
    if dsm is None or dsm.get("grid") is None:
        return None
    try:
        strook = lijn.buffer(tot, single_sided=True) if kant > 0 else lijn.buffer(-tot, single_sided=True)
        strook = strook.difference(lijn.buffer(van)).intersection(poly.buffer(-0.5))
    except Exception:
        return None
    if strook.is_empty or strook.area < 1.0:
        return None
    g, (minx, miny, maxx, maxy), res = dsm["grid"], dsm["bbox"], dsm["res"]
    m = np.zeros(g.shape, np.uint8)
    for p in getattr(strook, "geoms", [strook]):
        if p.geom_type != "Polygon":
            continue
        pts = np.array([[(x-minx)/res, (maxy-y)/res] for x, y in p.exterior.coords], np.int32)
        cv2.fillPoly(m, [pts], 1)
    v = g[(m > 0) & ~np.isnan(g)]
    return float(np.median(v)) if v.size >= 4 else None


def _min_breedte(p):
    c = list(p.minimum_rotated_rectangle.exterior.coords)
    return min(math.dist(c[0], c[1]), math.dist(c[1], c[2]))


def splitslijnen(poly, lijnen, dsm=None):
    """Kandidaat-splitslijnen voor één dakvlak met hun bewijs."""
    kand = []
    as_g = s2.hoofdas(poly) % 180.0
    for l in lijnen:
        if l["lijn"].distance(poly) > 0.3 or _langs_rand(l["lijn"], poly):
            continue
        # gebouwen zijn haaks: een schuine lichte lijn (rand van een buurdak, reflectie) splitst niet
        if min(_hoekverschil(l["hoek"], as_g), _hoekverschil(l["hoek"], as_g + 90.0)) > AS_TOL:
            continue
        door = _doorsnijding(l["lijn"], poly)
        if door is None or door.length < 3.0:
            continue
        kand.append((l, door))
    groepen = []
    for l, door in kand:
        for g in groepen:
            if _hoekverschil(g["hoek"], l["hoek"]) <= HOEK_TOL and \
               g["door"].distance(l["lijn"].centroid) <= OFFSET_TOL_M:
                g["leden"].append(l)
                break
        else:
            groepen.append({"hoek": l["hoek"], "door": door, "leden": [l]})
    uit = []
    for g in groepen:
        door = g["door"]
        zicht = unary_union([m["lijn"] for m in g["leden"]]).intersection(door.buffer(OFFSET_TOL_M))
        dekking = min(1.0, sum(m["lengte"] for m in g["leden"]) / door.length)
        a, b = Point(door.coords[0]), Point(door.coords[-1])
        eind_a = any(m["lijn"].distance(a) <= RAND_TOL_M for m in g["leden"])
        eind_b = any(m["lijn"].distance(b) <= RAND_TOL_M for m in g["leden"])
        h1, h2 = _zijhoogte(dsm, door, poly, +1), _zijhoogte(dsm, door, poly, -1)
        sprong = abs(h1 - h2) if (h1 is not None and h2 is not None) else None
        reden = None
        if eind_a and eind_b:
            reden = "opstand zichtbaar aan beide uiteinden"
        elif dekking >= 0.5 and (eind_a or eind_b):
            reden = f"opstand over {dekking:.0%} zichtbaar"
        elif dekking >= 0.15 and sprong is not None and sprong >= SPRONG_SPLIT_M:
            reden = f"opstand deels zichtbaar + hoogtesprong {sprong:.2f} m"
        if reden:
            uit.append({"lijn": door, "reden": reden, "dekking": round(dekking, 2),
                        "sprong_m": round(sprong, 2) if sprong is not None else None,
                        "zicht": zicht})
    return uit


def splits(poly, lijnen, dsm=None, max_n=4):
    """Splitslijnen worden beoordeeld op het ORIGINELE dakvlak (niet op de delen,
    anders wordt een losse leiding na de eerste splitsing ineens 'randvast').
    -> ([delen], [gebruikte splitslijnen])"""
    delen, gebruikt = [poly], []
    if poly.area < MIN_SPLIT_VLAK_M2:
        return delen, gebruikt
    for s in splitslijnen(poly, lijnen, dsm)[:max_n]:
        (x0, y0), (x1, y1) = s["lijn"].coords[0], s["lijn"].coords[-1]
        dl = math.hypot(x1-x0, y1-y0) or 1.0
        ex, ey = (x1-x0)/dl*0.3, (y1-y0)/dl*0.3
        mes = LineString([(x0-ex, y0-ey), (x1+ex, y1+ey)])
        nieuw, raak = [], False
        for d in delen:
            try:
                r = _split(d, mes)
            except Exception:
                nieuw.append(d); continue
            stuk = [g for g in getattr(r, "geoms", [r]) if g.geom_type == "Polygon"]
            if len(stuk) < 2 or min(p.area for p in stuk) < MIN_DEEL_M2 or \
               min(_min_breedte(p) for p in stuk) < MIN_DEEL_BREED_M:
                nieuw.append(d); continue
            nieuw += stuk; raak = True
        if raak:
            delen = nieuw
            gebruikt.append(s)
    return delen, gebruikt


def _in_randstrook(obj_poly, footprint, strook=1.5):
    """Langwerpig object dat geheel in de strook langs de buitenrand ligt en
    evenwijdig aan die rand loopt: daktrim/boeideel/dakrand, geen object."""
    c = list(obj_poly.minimum_rotated_rectangle.exterior.coords)
    z1, z2 = math.dist(c[0], c[1]), math.dist(c[1], c[2])
    if max(z1, z2) < 3.0*min(z1, z2):
        return False
    rand = footprint.exterior if footprint.geom_type == "Polygon" else \
        unary_union([g.exterior for g in footprint.geoms])
    if max(rand.distance(Point(p)) for p in c[:4]) > strook:
        return False
    ha = _hoek(c[0], c[1]) if z1 >= z2 else _hoek(c[1], c[2])
    p = rand.interpolate(rand.project(obj_poly.centroid))
    q = rand.interpolate(min(rand.length, rand.project(obj_poly.centroid) + 0.5))
    if p.distance(q) < 0.1:
        q = rand.interpolate(max(0.0, rand.project(obj_poly.centroid) - 0.5))
    return _hoekverschil(ha, _hoek((p.x, p.y), (q.x, q.y))) <= 10


def is_opstand(obj_poly, lijnen, dakvlakken_polys, footprint, obj=None):
    """Object dat grotendeels op een opstandlijn ligt die aan een rand vastzit
    (of langs de rand loopt), of een lange smalle strook langs de buitenrand
    -> geen object maar opstand/daktrim."""
    if obj_poly.area <= 0:
        return False
    if (obj is None or obj.get("type") != "zonnepaneel") and _in_randstrook(obj_poly, footprint):
        return True
    for l in lijnen:
        vast = _langs_rand(l["lijn"], footprint) or _raakt_rand(l["lijn"], footprint) or \
            any(_raakt_rand(l["lijn"], p) or _langs_rand(l["lijn"], p) for p in dakvlakken_polys)
        if not vast:
            continue
        ov = obj_poly.intersection(l["rect"].buffer(0.25)).area
        if ov >= 0.4*obj_poly.area:
            return True
        # smal object langs de lijn (zelfde richting, ligt erop)
        c = list(obj_poly.minimum_rotated_rectangle.exterior.coords)
        z1, z2 = math.dist(c[0], c[1]), math.dist(c[1], c[2])
        ha = _hoek(c[0], c[1]) if z1 >= z2 else _hoek(c[1], c[2])
        if max(z1, z2) >= 2.5*min(z1, z2) and _hoekverschil(ha, l["hoek"]) <= 10 and \
           l["lijn"].distance(obj_poly.centroid) <= 1.0 and \
           l["lijn"].buffer(1.0).intersection(obj_poly).area >= 0.5*obj_poly.area:
            return True
        # hoogte-'object' direct naast de opstand: de opstand zelf in AHN (0,5 m-cellen
        # smeren hem uit) -> smal, evenwijdig, binnen 1,5 m en binnen de lengte van de lijn
        if min(z1, z2) <= 1.2 and max(z1, z2) >= 1.8*min(z1, z2) and \
           _hoekverschil(ha, l["hoek"]) <= 15 and l["lijn"].distance(obj_poly) <= 1.0:
            (x0, y0), (x1, y1) = l["lijn"].coords[0], l["lijn"].coords[-1]
            dl = math.hypot(x1-x0, y1-y0) or 1.0
            t = ((obj_poly.centroid.x-x0)*(x1-x0) + (obj_poly.centroid.y-y0)*(y1-y0)) / dl
            if -0.5 <= t <= dl + 0.5:
                return True
    return False


def verfijn(image_path, frame, footprint, dakvlakken, dsm=None, rings=None, buren=None):
    """Splits 3D BAG-dakvlakken op zichtbare opstanden en bouw de randtypering opnieuw.
    -> (dakvlakken, lijnen, info)"""
    info = {"lijnen": 0, "splitsingen": []}
    try:
        lijnen = vind_lijnen(image_path, frame, footprint)
    except Exception as e:
        info["fout"] = str(e)
        return dakvlakken, [], info
    info["lijnen"] = len(lijnen)
    if not dakvlakken or not lijnen:
        return dakvlakken, lijnen, info
    import dakvlakken3d as d3
    nieuwe_rings, scheid = [], []
    gesplitst = False
    for v in dakvlakken:
        if not v.get("plat"):
            delen, gebruikt = [v["poly"]], []
        else:
            delen, gebruikt = splits(v["poly"], lijnen, dsm)
        a, b, c = v["fit"]
        for d in delen:
            ext = [(x, y, a*x + b*y + c) for x, y in d.exterior.coords]
            holes = [[(x, y, a*x + b*y + c) for x, y in h.coords] for h in d.interiors]
            nieuwe_rings.append((ext, holes, v.get("lod", "2.2")))
        for s in gebruikt:
            gesplitst = True
            scheid.append(s["lijn"])
            info["splitsingen"].append({"dakvlak": v["label"], "lengte_m": round(s["lijn"].length, 1),
                                        "reden": s["reden"]})
    if not gesplitst:
        return dakvlakken, lijnen, info
    nieuw = d3.bouw_dakvlakken(nieuwe_rings, buren or [])
    sl = unary_union(scheid).buffer(0.15)
    for v in nieuw:
        r = v["poly"].exterior.intersection(sl).length
        if r > 0.05:
            r = round(min(r, v["randen"].get("naad", 0.0)), 1)
            v["randen"]["naad"] = round(v["randen"]["naad"] - r, 1)
            v["randen"]["opstand_scheiding"] = r
            v["bron"] = "3D BAG + opstand op luchtfoto"
    return nieuw, lijnen, info


VERSION = "opstand-2026-10-20"
