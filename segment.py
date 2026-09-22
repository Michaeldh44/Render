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

    objs = []
    for kind, binm in (("zonnepaneel", donker), ("lichtstraat", licht)):
        for c in _contours(binm, roof):
            if cv2.contourArea(c) < 30:
                continue
            approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
            poly_rd = [_rd_of(p[0][0], p[0][1], W, H, frame) for p in approx]
            if len(poly_rd) < 3:
                continue
            try:
                shp = Polygon(poly_rd).buffer(0)
            except Exception:
                continue
            if shp.is_empty or shp.area < min_m2:
                continue
            rr = cv2.minAreaRect(c)               # (cx,cy),(w,h),hoek
            (wpx, hpx) = rr[1]
            objs.append({"type": kind, "poly_rd": [list(p) for p in poly_rd],
                         "m2": round(shp.area, 2),
                         "rd": (shp.centroid.x, shp.centroid.y)})
    return objs

VERSION = "r5-2026-09-21"
