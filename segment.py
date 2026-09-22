#!/usr/bin/env python3
"""
segment.py  —  MAATVASTE CONTOUREN uit de luchtfoto (beeldsegmentatie)
======================================================================
Haalt de echte omtrek van dakobjecten uit de PIXELS van de ortho, niet
uit een Vision-schatting. Donkere rechthoeken -> zonnepaneel, lichte
rechthoeken -> lichtstraat/koepel. Contour + oppervlak zijn maatvast
(gegeorefereerd via het beeld-frame naar RD).

  segmenteer(image_path, frame, footprint=None) -> [{type, poly_rd, m2, ...}]

Vision levert daarna alleen nog het LABEL (welke contour is wat); de
GEOMETRIE komt hier vandaan.
"""
import cv2
import numpy as np
from shapely.geometry import Polygon

CLUSTER_M = 0.8      # aaneenliggende panelen binnen deze afstand -> 1 veld
EXTENT_MIN = 0.62    # vlek moet zijn omhullende rechthoek zo goed vullen (anders = blob)
LICHT_MAX_M2 = 40.0  # lichtstraat/koepel groter dan dit is geen daglichtopening
ROOF_SHARE_MAX = 0.15  # één object > 15% van het dak = vrijwel zeker segmentatie-fout


def _rd_of(px, py, W, H, frame):
    xf, yf = px / W, py / H
    ox, oy = frame["o"]; dux, duy = frame["du"]; dvx, dvy = frame["dv"]
    return (ox + xf*dux + yf*dvx, oy + xf*duy + yf*dvy)


def _rd_to_px(x, y, W, H, frame):
    """Inverse: los xf,yf op uit o + xf*du + yf*dv = (x,y)."""
    ox, oy = frame["o"]; dux, duy = frame["du"]; dvx, dvy = frame["dv"]
    det = dux*dvy - duy*dvx
    if abs(det) < 1e-9:
        return (0, 0)
    bx, by = x - ox, y - oy
    xf = (bx*dvy - by*dvx) / det
    yf = (dux*by - duy*bx) / det
    return (xf*W, yf*H)


def _roofmask(W, H, frame, footprint):
    mask = np.zeros((H, W), np.uint8)
    polys = list(getattr(footprint, "geoms", [footprint]))
    for p in polys:
        pts = np.array([_rd_to_px(x, y, W, H, frame)
                        for (x, y) in p.exterior.coords], np.int32)
        cv2.fillPoly(mask, [pts], 255)
    cv2.erode(mask, np.ones((5, 5), np.uint8), mask, iterations=2)   # rand weg
    return mask


def _contours(binmask, roofmask):
    m = cv2.bitwise_and(binmask, roofmask)
    k = np.ones((5, 5), np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return cnts


def koppel_labels(contouren, vision_objs, max_afstand=3.0):
    """Geef elke (maatvaste) contour het passende Vision-label. Contour =
    geometrie (hard); Vision = alleen het label. Zonder match: eigen gok."""
    from shapely.geometry import Point, Polygon
    out = []
    for c in contouren:
        try:
            poly = Polygon(c["poly_rd"]).buffer(0)
        except Exception:
            continue
        match = None
        for v in vision_objs or []:                 # label dat IN de contour valt
            if v.get("rd") and poly.contains(Point(*v["rd"])):
                match = v
                break
        if match is None:                           # anders: dichtstbijzijnde label
            best, bd = None, 1e9
            for v in vision_objs or []:
                if not v.get("rd"):
                    continue
                dd = poly.centroid.distance(Point(*v["rd"]))
                if dd < bd:
                    best, bd = v, dd
            if best and bd <= max_afstand:
                match = best
        if match:
            typ = match.get("type", c["type"])
            oms = match.get("omschrijving", "")
            zk = "hoog"
        else:
            typ, oms, zk = c["type"], "(uit beeld, ongelabeld)", "midden"
        out.append({"type": typ, "omschrijving": oms, "zekerheid": zk,
                    "rd": c["rd"], "poly_rd": c["poly_rd"], "m2": c["m2"]})
    return out


def _mperpx(W, H, frame):
    import math
    dux, duy = frame["du"]; dvx, dvy = frame["dv"]
    return (math.hypot(dux, duy) / W + math.hypot(dvx, dvy) / H) / 2


def segmenteer(image_path, frame, footprint=None, min_m2=1.5):
    img = cv2.imread(image_path)
    if img is None:
        return []
    H, W = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    roof = _roofmask(W, H, frame, footprint) if footprint is not None \
        else np.full((H, W), 255, np.uint8)

    vals = gray[roof > 0]
    med = int(np.median(vals)) if vals.size else 128
    donker = ((gray < max(40, med - 55)).astype(np.uint8)) * 255   # panelen
    licht = ((gray > min(235, med + 55)).astype(np.uint8)) * 255   # daglicht

    mpp = _mperpx(W, H, frame)
    clus = max(3, int(round(CLUSTER_M / mpp)))        # cluster-kernel (panelen)
    roof_m2 = footprint.area if footprint is not None else None

    objs = []
    for kind, binm, extra_close in (("zonnepaneel", donker, clus),
                                    ("lichtstraat", licht, 0)):
        m = cv2.bitwise_and(binm, roof)
        k = np.ones((5, 5), np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
        if extra_close:                                # panelen samenvoegen
            kk = np.ones((extra_close, extra_close), np.uint8)
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kk)
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area_px = cv2.contourArea(c)
            if area_px < 30:
                continue
            rr = cv2.minAreaRect(c)                    # strakke rechthoek
            (wpx, hpx) = rr[1]
            rect_px = wpx * hpx
            if rect_px <= 0:
                continue
            extent = area_px / rect_px                 # hoe goed vult de vlek de rechthoek
            if extent < EXTENT_MIN:                    # geen strak object -> blob/artefact weg
                continue
            w_m, h_m = wpx * mpp, hpx * mpp
            m2 = round(w_m * h_m, 2)
            if m2 < min_m2:
                continue
            if roof_m2 and m2 > ROOF_SHARE_MAX * roof_m2:   # te groot voor één object
                continue
            if kind == "lichtstraat" and m2 > LICHT_MAX_M2:  # geen daglichtopening
                continue
            box = cv2.boxPoints(rr)                    # 4 rechte hoeken
            poly_rd = [_rd_of(float(px), float(py), W, H, frame) for (px, py) in box]
            cx, cy = rr[0]
            objs.append({"type": kind,
                         "poly_rd": [list(p) for p in poly_rd], "m2": m2,
                         "extent": round(extent, 2),
                         "rd": _rd_of(float(cx), float(cy), W, H, frame),
                         "afm_m": (round(max(w_m, h_m), 2), round(min(w_m, h_m), 2))})
    return objs



def combineer(contouren, vision_objs, max_afstand=3.0):
    """Segmentatie-contouren (met label) + Vision-objecten die GEEN contour
    kregen (bv. kleine doorvoeren) als los blok, zodat die niet verdwijnen."""
    from shapely.geometry import Point, Polygon as _P
    labeled = koppel_labels(contouren, vision_objs, max_afstand)
    polys = []
    for c in contouren:
        try:
            polys.append(_P(c["poly_rd"]).buffer(0.5))
        except Exception:
            pass
    rest = []
    for v in vision_objs or []:
        if not v.get("rd"):
            continue
        p = Point(*v["rd"])
        if not any(pl.contains(p) for pl in polys):
            rest.append({"type": v.get("type", "overig"),
                         "omschrijving": v.get("omschrijving", ""),
                         "zekerheid": v.get("zekerheid", "laag"),
                         "rd": v["rd"], "grootte_m": v.get("grootte_m") or (0.6, 0.6)})
    return labeled + rest


VERSION = "r6-2026-09-22"
