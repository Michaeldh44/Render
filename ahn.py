#!/usr/bin/env python3
"""
ahn.py  —  AHN-hoogtecheck (Route 2)  [onafhankelijke bron]
============================================================
Haalt het AHN-hoogteraster (DSM = dak-oppervlak incl. objecten) op via de
PDOK WCS en gebruikt het als ONAFHANKELIJKE controle naast segmenter en Vision:

  1) meet per object of het ECHT omhoog steekt boven het dakvlak (opsteek).
     - steekt omhoog  -> ahn-hoogteclaim (mag naar maatklasse A)
     - vlak, terwijl het type juist zou moeten uitsteken (lichtstraat/koepel/
       installatie/schoorsteen) -> ahn keurt af -> object 'betwijfeld' (gemeld)
  2) leidt het AFSCHOT af uit het hoogteverloop over het kale dakvlak
     (vervangt de 'n.t.b.'-regel op het blad).

PDOK WCS (keyless):  https://service.pdok.nl/rws/ahn/wcs/v1_0
  laag dsm_05m (0,5 m oppervlak), GeoTIFF float32 in meters NAP, EPSG:28992.

AHN is periodiek ingemeten, dus NIET dagvers: recente panelen/units kunnen
ontbreken. Daarom een claim NAAST de andere, geen vervanger. Geen netwerk of
een fout -> lege meting; de rest van de scan werkt gewoon door.
"""
import os
import numpy as np

WCS = "https://service.pdok.nl/rws/ahn/wcs/v1_0"
COVERAGE_DSM = os.environ.get("DAKSCAN_AHN_COVERAGE", "dsm_05m")
RES = 0.5                    # m per cel (AHN 0,5 m)
OPSTEEK_MIN = 0.30          # >= 30 cm boven dakvlak = duidelijk verhoogd object
VLAK_MAX = 0.15            # < 15 cm = vlak (voor types die juist horen uit te steken)
UITSTEKEND = {"lichtstraat", "lichtkoepel", "installatie", "schoorsteen", "dakraam"}


def _lees_raster(pad):
    """Lees een (mogelijk lastige) float32-GeoTIFF. tifffile eerst (betrouwbaar
    voor AHN), dan PIL, dan OpenCV. Geeft np.ndarray of None."""
    try:
        import tifffile
        return np.asarray(tifffile.imread(pad))
    except Exception:
        pass
    try:
        from PIL import Image
        a = np.array(Image.open(pad))
        if a.size:
            return a
    except Exception:
        pass
    try:
        import cv2
        a = cv2.imread(pad, cv2.IMREAD_UNCHANGED)
        if a is not None:
            return a
    except Exception:
        pass
    return None


def _wcs_exception(content):
    """Herken een WCS/OWS-foutmelding (MapServer geeft die soms met HTTP 200)."""
    head = content[:2000]
    if head[:200].lstrip()[:5] == b"<?xml" or b"ServiceException" in head or b"Exception" in head:
        import re
        txt = content.decode("utf-8", "ignore")
        m = (re.search(r"ServiceException[^>]*>(.*?)</", txt, re.S)
             or re.search(r"ExceptionText>(.*?)</", txt, re.S))
        return (m.group(1).strip() if m else txt[:200])[:200]
    return None


def haal_dsm(bounds, marge=3.0, timeout=60):
    """Haal het DSM-raster voor bounds (minx,miny,maxx,maxy in RD).
    Geeft {grid, bbox, res, status} of {grid:None, status:reden} bij een fout.
    grid[row][col]: row 0 = noord (maxy). NoData -> NaN."""
    import requests
    minx, miny, maxx, maxy = bounds
    minx -= marge; miny -= marge; maxx += marge; maxy += marge
    W = max(1, int(round((maxx - minx) / RES)))
    H = max(1, int(round((maxy - miny) / RES)))
    fmt = os.environ.get("DAKSCAN_AHN_FORMAT", "GEOTIFF_FLOAT32")
    params = {"SERVICE": "WCS", "VERSION": "1.0.0", "REQUEST": "GetCoverage",
              "FORMAT": fmt, "COVERAGE": COVERAGE_DSM,
              "BBOX": f"{minx},{miny},{maxx},{maxy}",
              "CRS": "EPSG:28992", "RESPONSE_CRS": "EPSG:28992",
              "WIDTH": str(W), "HEIGHT": str(H)}
    try:
        r = requests.get(WCS, params=params, timeout=timeout)
    except requests.RequestException as e:
        return {"grid": None, "status": f"netwerkfout: {e}"}
    if r.status_code != 200 or not r.content:
        return {"grid": None, "status": f"fout {r.status_code}: {r.text[:200]}"}
    fout = _wcs_exception(r.content)
    if fout:
        return {"grid": None, "status": f"WCS-exception: {fout}"}
    pad = "/tmp/ahn_dsm.tif"
    open(pad, "wb").write(r.content)
    arr = _lees_raster(pad)
    if arr is None:
        ct = r.headers.get("content-type", "?")
        kop = r.content[:16].hex()
        return {"grid": None,
                "status": f"raster onleesbaar (content-type={ct}, {len(r.content)} bytes, "
                          f"begin={kop})"}
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    arr[(arr > 1e4) | (arr < -1e3)] = np.nan          # PDOK nodata (3.4e38 / -32768)
    return {"grid": arr, "bbox": (minx, miny, maxx, maxy), "res": RES,
            "H": arr.shape[0], "W": arr.shape[1], "status": "ok"}


def _poly_naar_cellen(poly_rd, info):
    """RD-polygoon -> (col,row)-punten in het raster."""
    minx, miny, maxx, maxy = info["bbox"]
    res = info["res"]
    pts = []
    for (x, y) in poly_rd:
        col = (x - minx) / res
        row = (maxy - y) / res                         # row 0 = noord
        pts.append([col, row])
    return np.array(pts, dtype=np.int32)


def _masker(poly_rd, info):
    import cv2
    m = np.zeros((info["H"], info["W"]), np.uint8)
    cv2.fillPoly(m, [_poly_naar_cellen(poly_rd, info)], 1)
    return m.astype(bool)


def _mediaan(grid, mask):
    v = grid[mask]
    v = v[~np.isnan(v)]
    return float(np.median(v)) if v.size else None


def dakvlak_hoogte(footprint, info, objecten):
    """Basishoogte van het KALE dak (footprint minus de objecten)."""
    polys = list(getattr(footprint, "geoms", [footprint]))
    roof = np.zeros((info["H"], info["W"]), bool)
    for p in polys:
        roof |= _masker(list(p.exterior.coords), info)
    for o in objecten:
        if o.get("poly_rd"):
            roof &= ~_masker(o["poly_rd"], info)
    return _mediaan(info["grid"], roof), roof


def meet_objecten(objecten, info, basis):
    """Per object: opsteek boven het dakvlak. Geeft {obj_id: {...}}.
    verhoogd=True -> hoogteclaim; afkeuren=True -> AHN betwijfelt (vlak terwijl
    het type hoort uit te steken)."""
    uit = {}
    for o in objecten:
        if not o.get("poly_rd") or not o.get("id"):
            continue
        h = _mediaan(info["grid"], _masker(o["poly_rd"], info))
        if h is None or basis is None:
            continue
        opsteek = round(h - basis, 2)
        verhoogd = opsteek >= OPSTEEK_MIN
        afkeuren = (not verhoogd and opsteek < VLAK_MAX
                    and o.get("type") in UITSTEKEND)
        uit[o["id"]] = {"opsteek_m": opsteek, "nap_m": round(h, 2),
                        "verhoogd": bool(verhoogd), "afkeuren": bool(afkeuren)}
    return uit


def afschot(footprint, info, objecten, basis_mask=None):
    """Afschot uit een vlakfit op het kale dak. Geeft {promille, procent,
    richting_graden, mm_per_m} of None."""
    if basis_mask is None:
        _, basis_mask = dakvlak_hoogte(footprint, info, objecten)
    grid = info["grid"]
    rows, cols = np.where(basis_mask & ~np.isnan(grid))
    if rows.size < 20:
        return None
    minx, miny, maxx, maxy = info["bbox"]; res = info["res"]
    X = minx + (cols + 0.5) * res
    Y = maxy - (rows + 0.5) * res
    Z = grid[rows, cols]
    X0, Y0 = X.mean(), Y.mean()
    A = np.c_[X - X0, Y - Y0, np.ones(X.size)]
    try:
        (a, b, _), *_ = np.linalg.lstsq(A, Z, rcond=None)
    except Exception:
        return None
    helling = float(np.hypot(a, b))                    # m per m
    richting = float((np.degrees(np.arctan2(-b, -a))) % 360)  # richting van afstroming
    return {"mm_per_m": round(helling * 1000, 1), "procent": round(helling * 100, 2),
            "promille": round(helling * 1000, 1), "richting_graden": round(richting, 0)}


def analyse(footprint, objecten, bounds=None):
    """Alles-in-één: DSM ophalen, objecten meten, afschot bepalen.
    Geeft {status, metingen:{obj_id:{...}}, afschot:{...}, basis_nap}."""
    info = haal_dsm(bounds or footprint.bounds)
    if not info or info.get("grid") is None:
        return {"status": info.get("status", "geen data") if info else "geen data",
                "metingen": {}, "afschot": None, "basis_nap": None}
    basis, basis_mask = dakvlak_hoogte(footprint, info, objecten)
    metingen = meet_objecten(objecten, info, basis)
    afs = afschot(footprint, info, objecten, basis_mask)
    return {"status": "ok", "metingen": metingen, "afschot": afs,
            "basis_nap": round(basis, 2) if basis is not None else None}


VERSION = "r6-2026-09-22"
