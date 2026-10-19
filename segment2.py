#!/usr/bin/env python3
"""
segment2.py  —  OBJECTHERKENNING v2: hoogte + kleur + schaduwlogica
===================================================================
Waarom v2: v1 deed alleen 'donker = zonnepaneel, licht = lichtstraat' op
grijswaarde. Een schaduw is donker en werd dus een zonnepaneel; vuile
lichtstraten werden gemist; elk vlekje kreeg een eigen scheve rechthoek.

v2 werkt per dakvlak (uit 3D BAG) en combineert drie onafhankelijke signalen:
  1. HOOGTE  — AHN-DSM min het gefitte dakvlak (nDSM). Echte objecten steken
               uit (units, koepels, lichtstraten, schuine panelen); een schaduw
               en dakhuid niet.
  2. KLEUR   — Lab t.o.v. het kale dak: panelen zijn donker en blauwig, glas
               is licht; een schaduw is donkerder maar houdt de kleurtoon.
  3. ZON     — PDOK-luchtfoto's zijn rond het middaguur gevlogen: schaduw valt
               NOORD van wat hem werpt. Donker zonder hoogte met een 'werper'
               (object of dakrand) direct ten zuiden = schaduw -> weg.
Elke rechthoek wordt uitgelijnd op de hoofdrichting van het dakvlak.

  detecteer(image_path, frame, footprint, dakvlakken=None, dsm=None)
      -> [{type, poly_rd, m2, rd, afm_m, opsteek_m, bron, zekerheid, omschrijving}]
"""
import math
import numpy as np
import cv2
from shapely.geometry import Polygon, MultiPoint
from shapely.affinity import rotate as _rot
from shapely.ops import unary_union

H_OBJ = 0.25            # nDSM >= 25 cm = verhoogd object
H_VLAK = 0.12           # nDSM <  12 cm = vlak (dakhuid / schaduw)
RAND_MARGE_M = 0.6      # strook langs de dakrand die we niet als object zien
SCHADUW_ZOEK_M = 2.5    # zoek tot zoveel meter ten zuiden naar een werper
MIN_OBJ_M2 = 0.8
MIN_PV_M2 = 1.5
PV_CLUSTER_M = 0.8
MAX_AANDEEL = 0.20      # één object > 20% van het dakvlak = vrijwel zeker fout
KOEPEL_MAX_ZIJDE = 2.2


# ------------------------------------------------------------- frame-hulp
def _rd_of(px, py, W, H, f):
    xf, yf = px / W, py / H
    return (f["o"][0] + xf*f["du"][0] + yf*f["dv"][0], f["o"][1] + xf*f["du"][1] + yf*f["dv"][1])


def _rd_to_px(x, y, W, H, f):
    (ox, oy), (dux, duy), (dvx, dvy) = f["o"], f["du"], f["dv"]
    det = dux*dvy - duy*dvx
    if abs(det) < 1e-9:
        return (0.0, 0.0)
    bx, by = x - ox, y - oy
    return ((bx*dvy - by*dvx)/det*W, (dux*by - duy*bx)/det*H)


def _mpp(W, H, f):
    return (math.hypot(*f["du"])/W + math.hypot(*f["dv"])/H) / 2


def _poly_mask(poly, W, H, f, erode_px=0):
    m = np.zeros((H, W), np.uint8)
    for p in getattr(poly, "geoms", [poly]):
        pts = np.array([_rd_to_px(x, y, W, H, f) for x, y in p.exterior.coords], np.int32)
        cv2.fillPoly(m, [pts], 255)
        for hole in p.interiors:
            hp = np.array([_rd_to_px(x, y, W, H, f) for x, y in hole.coords], np.int32)
            cv2.fillPoly(m, [hp], 0)
    if erode_px > 0:
        m = cv2.erode(m, np.ones((3, 3), np.uint8), iterations=int(erode_px))
    return m > 0


def hoofdas(poly):
    """Hoek (graden, RD) van de langste zijde van de omhullende rechthoek."""
    c = list(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    e0 = (c[1][0]-c[0][0], c[1][1]-c[0][1]); e1 = (c[2][0]-c[1][0], c[2][1]-c[1][1])
    e = e0 if math.hypot(*e0) >= math.hypot(*e1) else e1
    return math.degrees(math.atan2(e[1], e[0]))


def uitgelijnde_rechthoek(pts_rd, as_graden):
    """Kleinste rechthoek om de punten, met zijden evenwijdig aan de dakvlak-as."""
    if len(pts_rd) < 3:
        return None
    mp = MultiPoint(pts_rd)
    c = mp.centroid
    r = _rot(mp, -as_graden, origin=c)
    minx, miny, maxx, maxy = r.bounds
    if maxx - minx < 1e-3 or maxy - miny < 1e-3:
        return None
    b = Polygon([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)])
    return _rot(b, as_graden, origin=c)


# ------------------------------------------------------------- hoogte (AHN)
def _ndsm_in_beeld(dsm, dakvlak, W, H, f):
    """nDSM (m boven het gefitte dakvlak) op beeldpixels, NaN buiten dit dakvlak.
    Robuuste vlakfit: 2x herhalen zonder uitschieters (objecten, randen)."""
    grid, (minx, miny, maxx, maxy), res = dsm["grid"], dsm["bbox"], dsm["res"]
    Hc, Wc = grid.shape
    # dakvlakcellen (1 m ingekrompen, zodat randen/borstweringen de fit niet sturen)
    kern = dakvlak.buffer(-1.0)
    if kern.is_empty or kern.area < 4:
        kern = dakvlak
    cm = np.zeros((Hc, Wc), np.uint8)
    for p in getattr(kern, "geoms", [kern]):
        pts = np.array([[(x-minx)/res, (maxy-y)/res] for x, y in p.exterior.coords], np.int32)
        cv2.fillPoly(cm, [pts], 1)
    rows, cols = np.where((cm > 0) & ~np.isnan(grid))
    if rows.size < 12:
        return None
    X = minx + (cols+0.5)*res; Y = maxy - (rows+0.5)*res; Z = grid[rows, cols].astype(np.float64)
    keep = np.ones(Z.size, bool)
    coef = None
    for _ in range(3):
        A = np.c_[X[keep], Y[keep], np.ones(keep.sum())]
        if A.shape[0] < 12:
            break
        coef, *_ = np.linalg.lstsq(A, Z[keep], rcond=None)
        resid = Z - (coef[0]*X + coef[1]*Y + coef[2])
        keep = np.abs(resid) < 0.15
    if coef is None:
        return None
    # beeldpixels -> RD -> DSM-cel
    yy, xx = np.mgrid[0:H, 0:W]
    xf, yf = (xx+0.5)/W, (yy+0.5)/H
    RX = f["o"][0] + xf*f["du"][0] + yf*f["dv"][0]
    RY = f["o"][1] + xf*f["du"][1] + yf*f["dv"][1]
    ci = np.clip(((RX-minx)/res).astype(int), 0, Wc-1)
    ri = np.clip(((maxy-RY)/res).astype(int), 0, Hc-1)
    nd = grid[ri, ci] - (coef[0]*RX + coef[1]*RY + coef[2])
    return nd.astype(np.float32), coef


# ------------------------------------------------------------- hoofdfunctie
def detecteer(image_path, frame, footprint, dakvlakken=None, dsm=None):
    img = cv2.imread(image_path)
    if img is None:
        return []
    H, W = img.shape[:2]
    mpp = _mpp(W, H, frame)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, A, B = lab[..., 0], lab[..., 1], lab[..., 2]

    vlakken = [d["poly"] for d in (dakvlakken or []) if d.get("poly") is not None] \
        or list(getattr(footprint, "geoms", [footprint]))
    rand_px = max(1, int(round(RAND_MARGE_M / mpp)))
    zoek_px = max(2, int(round(SCHADUW_ZOEK_M / mpp)))

    # richting 'zuid' in beeldpixels (schaduw valt de andere kant op)
    c = footprint.centroid
    p0 = _rd_to_px(c.x, c.y, W, H, frame); p1 = _rd_to_px(c.x, c.y - 10.0, W, H, frame)
    sz = np.array([p1[0]-p0[0], p1[1]-p0[1]]); sz = sz / (np.linalg.norm(sz) or 1.0)

    alle = []
    dakhuid_totaal = np.zeros((H, W), bool)
    for vlak in vlakken:
        if vlak.area < 4:
            continue
        as_g = hoofdas(vlak)
        vol = _poly_mask(vlak, W, H, frame)
        dak = _poly_mask(vlak, W, H, frame, erode_px=rand_px)     # zonder randstrook
        if dak.sum() < 50:
            continue
        dakhuid_totaal |= dak
        rand = vol & ~dak                                          # randstrook (werpt schaduw)

        # referentie: kaal dak = mediaan Lab binnen het dakvlak
        Lr, Ar, Br = (float(np.median(ch[dak])) for ch in (L, A, B))
        dL = max(22.0, 0.22*Lr)
        donker = dak & (L < Lr - dL)
        licht = dak & (L > Lr + max(22.0, 0.18*(255-Lr)))
        chroma = np.sqrt((A-Ar)**2 + (B-Br)**2)
        blauwig = (B - Br) < -4.0

        nd = None
        if dsm is not None and dsm.get("grid") is not None:
            r = _ndsm_in_beeld(dsm, vlak, W, H, frame)
            if r is not None:
                nd = r[0]
        hoog = (dak & (nd >= H_OBJ)) if nd is not None else np.zeros((H, W), bool)
        vlakh = (nd < H_VLAK) if nd is not None else np.ones((H, W), bool)

        # ---- schaduw: donker, niet verhoogd, met een werper direct ten zuiden
        werper = hoog | rand | licht
        heeft_werper = np.zeros((H, W), bool)
        for k in range(2, zoek_px+1, max(1, zoek_px//8)):
            dx, dy = int(round(sz[0]*k)), int(round(sz[1]*k))
            M = np.float32([[1, 0, -dx], [0, 1, -dy]])           # kijk k px richting zuid
            heeft_werper |= cv2.warpAffine(werper.astype(np.uint8), M, (W, H),
                                           flags=cv2.INTER_NEAREST, borderValue=0) > 0
        schaduw = donker & vlakh & heeft_werper & (chroma < 9.0)

        k5 = np.ones((5, 5), np.uint8)

        def comps(mask, close=2):
            m = cv2.morphologyEx(mask.astype(np.uint8)*255, cv2.MORPH_OPEN, k5)
            if close:
                m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k5, iterations=close)
            n, lab_, stats, _ = cv2.connectedComponentsWithStats(m, 8)
            for i in range(1, n):
                if stats[i, cv2.CC_STAT_AREA] * mpp*mpp < 0.4:
                    continue
                yield lab_ == i

        def maak(mask, typ, bron, zek, oms=""):
            ys, xs = np.where(mask)
            if xs.size < 8:
                return None
            # randpunten volstaan voor de rechthoek
            sel = np.linspace(0, xs.size-1, min(xs.size, 4000)).astype(int)
            pts = [_rd_of(float(xs[i])+0.5, float(ys[i])+0.5, W, H, frame) for i in sel]
            rect = uitgelijnde_rechthoek(pts, as_g)
            if rect is None:
                return None
            area_mask = xs.size * mpp*mpp
            vul = area_mask / rect.area if rect.area else 0
            o = {"type": typ, "poly_rd": [list(p) for p in list(rect.exterior.coords)[:4]],
                 "m2": round(rect.area, 2), "extent": round(vul, 2),
                 "rd": tuple(rect.centroid.coords[0]), "bron": bron, "zekerheid": zek,
                 "omschrijving": oms, "dakvlak_as": round(as_g, 1)}
            b = rect.minimum_rotated_rectangle.exterior.coords
            z1 = math.dist(b[0], b[1]); z2 = math.dist(b[1], b[2])
            o["afm_m"] = (round(max(z1, z2), 2), round(min(z1, z2), 2))
            if nd is not None:
                v = nd[mask]; v = v[~np.isnan(v)]
                o["opsteek_m"] = round(float(np.median(v)), 2) if v.size else None
            return o

        gebruikt = np.zeros((H, W), bool)
        # ---- 1) verhoogde objecten (AHN): type uit de kleur in en rond het object
        def soort_licht(mask_licht, o):
            """Lichtstraat/koepel of (lichte) installatie: glas is zeer licht en/of
            langwerpig en steekt meestal < 0,9 m uit; units zijn hoger en gedrongen."""
            l_l = float(np.median(L[mask_licht])) if mask_licht.any() else Lr
            asp = (o["afm_m"][0] / o["afm_m"][1]) if o["afm_m"][1] else 1
            zeer_licht = l_l > Lr + 0.55*(255 - Lr)
            ops = o.get("opsteek_m")
            if ops is not None and ops > 0.9 and asp < 3:
                return "installatie"
            if zeer_licht or asp >= 3:
                return "lichtkoepel" if (max(o["afm_m"]) <= KOEPEL_MAX_ZIJDE and o["m2"] < 5) else "lichtstraat"
            return "installatie"

        for cm in comps(hoog, close=1):
            zone = cv2.dilate(cm.astype(np.uint8), k5, iterations=max(1, int(0.4/mpp))) > 0
            zone &= dak
            # alleen wat echt omhoog staat telt mee voor de vorm (geen schaduw/dakhuid ernaast)
            omhoog = zone & (nd >= H_VLAK)
            ml = licht & omhoog
            md = donker & omhoog
            gemaakt = False
            # één hoogteblok kan meerdere objecten bevatten (paneelveld naast lichtstraat): splits op kleur
            if ml.sum() * mpp*mpp >= 0.5*MIN_OBJ_M2:
                for deel in comps(ml, close=1):
                    o = maak(deel, "lichtstraat", "ahn+beeld", "hoog")
                    if o and o["m2"] >= MIN_OBJ_M2 and o["m2"] <= MAX_AANDEEL*vlak.area:
                        o["type"] = soort_licht(deel, o)
                        if o["type"] == "installatie":
                            o["zekerheid"] = "midden"
                        alle.append(o); gemaakt = True
            if md.sum() * mpp*mpp >= 0.5*MIN_PV_M2:
                # kleine kernel: paneelrijen sluiten wel aan, een schaduwstrook op 0,5 m niet
                kk = max(3, int(round(0.4/mpp)))
                veld = cv2.morphologyEx(md.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((kk, kk), np.uint8)) > 0
                veld &= omhoog
                for deel in comps(veld, close=0):
                    if not _is_paneelkleur(L, B, deel & md, Lr, Br):
                        continue
                    o = maak(deel, "zonnepaneel", "ahn+beeld", "hoog", "paneelveld (omhullende)")
                    if o and o["m2"] >= MIN_PV_M2 and o["m2"] <= MAX_AANDEEL*vlak.area:
                        alle.append(o); gemaakt = True
            if not gemaakt:
                o = maak(cm, "installatie", "ahn", "midden")
                if o and o["m2"] >= MIN_OBJ_M2 and o["m2"] <= MAX_AANDEEL*vlak.area:
                    alle.append(o)
            gebruikt |= zone

        # ---- 2) alleen beeld: panelen (donker, blauwig, geen schaduw)
        pv_kand = donker & ~schaduw & ~gebruikt
        for cm in comps(pv_kand, close=max(2, int(PV_CLUSTER_M/mpp/5))):
            if not _is_paneelkleur(L, B, cm, Lr, Br):
                continue
            o = maak(cm, "zonnepaneel", "beeld", "midden" if nd is None else "laag",
                     "alleen luchtfoto (geen hoogte gemeten)")
            if o and o["m2"] >= MIN_PV_M2 and o["extent"] >= 0.55 and o["m2"] <= MAX_AANDEEL*vlak.area:
                alle.append(o); gebruikt |= cm

        # ---- 3) alleen beeld: licht + eigen schaduw aan de noordkant = verhoogd = lichtstraat
        for cm in comps(licht & ~gebruikt, close=1):
            noord = np.zeros((H, W), bool)
            for k in range(2, max(3, int(1.2/mpp)), 2):
                dx, dy = int(round(-sz[0]*k)), int(round(-sz[1]*k))
                M = np.float32([[1, 0, dx], [0, 1, dy]])
                noord |= cv2.warpAffine(cm.astype(np.uint8), M, (W, H),
                                        flags=cv2.INTER_NEAREST, borderValue=0) > 0
            eigen_schaduw = (noord & ~cm & donker).sum() > 0.15*cm.sum()
            if not eigen_schaduw:
                continue                          # vlakke lichte vlek (grind, coating, reflectie): weg
            o = maak(cm, "lichtstraat", "beeld", "midden", "licht object met eigen schaduw (verhoogd)")
            if not o or o["extent"] < 0.6 or o["m2"] < MIN_OBJ_M2 or o["m2"] > 40:
                continue
            o["type"] = soort_licht(cm, o)
            alle.append(o)

    return _voeg_pv_samen(alle)


def _is_paneelkleur(L, B, mask, Lr, Br):
    """Panelen: als geheel blauwig, of als geheel duidelijk donker t.o.v. het dak
    (donkergrijze/zwarte panelen). Beoordeeld op de mediaan van het hele veld,
    niet per pixel (randen, reflecties en frames wijken per pixel af)."""
    if not mask.any():
        return False
    mb = float(np.median(B[mask])); ml = float(np.median(L[mask]))
    return (mb - Br) < -3.0 or ml < Lr - max(30.0, 0.3*Lr)


def _voeg_pv_samen(objs):
    """Losse paneelrechthoeken die (bijna) raken samenvoegen tot één veld per dakvlak-as."""
    pv = [o for o in objs if o["type"] == "zonnepaneel"]
    rest = [o for o in objs if o["type"] != "zonnepaneel"]
    groepen = []
    for o in pv:
        p = Polygon(o["poly_rd"]).buffer(PV_CLUSTER_M/2)
        hit = [g for g in groepen if g["buf"].intersects(p) and g["as"] == o.get("dakvlak_as")]
        if hit:
            g = hit[0]
            for h in hit[1:]:
                g["leden"] += h["leden"]; g["buf"] = g["buf"].union(h["buf"]); groepen.remove(h)
            g["leden"].append(o); g["buf"] = g["buf"].union(p)
        else:
            groepen.append({"leden": [o], "buf": p, "as": o.get("dakvlak_as")})
    uit = []
    for g in groepen:
        if len(g["leden"]) == 1:
            uit.append(g["leden"][0]); continue
        pts = [tuple(c) for o in g["leden"] for c in o["poly_rd"]]
        rect = uitgelijnde_rechthoek(pts, g["as"] or 0.0)
        best = max(g["leden"], key=lambda o: o["m2"])
        n = dict(best)
        n["poly_rd"] = [list(p) for p in list(rect.exterior.coords)[:4]]
        n["m2"] = round(sum(o["m2"] for o in g["leden"]), 2)      # paneeloppervlak, niet de omhullende
        n["rd"] = tuple(rect.centroid.coords[0])
        n["omschrijving"] = f"veld van {len(g['leden'])} delen"
        uit.append(n)
    return rest + uit


VERSION = "v2-2026-10-19"
