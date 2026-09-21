#!/usr/bin/env python3
"""
luchtfoto.py  —  DAKVISUAL uit een echte luchtfoto (PDOK)
=========================================================
Haalt een loodrechte lucht-ortho (PDOK, ~8 cm) op over de dak-bbox en
legt de meet-footprint eroverheen, zodat je ziet dat het model op het
echte dak geregistreerd staat.

  haal(bounds_rd, outfile, footprint=..., stand_in=False)

Voor NL is dit scherper en juister dan satelliet (nadir-ortho).
LET OP: net als bij Nisse is dit een ILLUSTRATIE, geen maatbron.

De echte GetMap kon ik hier niet draaien (geen PDOK-toegang). Endpoint
staat als constante; met stand_in=True bewijs je de registratie zonder
netwerk.
"""
import io, requests
from PIL import Image, ImageDraw, ImageFont
from shapely.ops import unary_union

# PDOK Actuele Luchtfoto RGB (WMS). Pas layer aan: Actueel_orthoHR (~8cm) of Actueel_ortho25.
LUCHTFOTO_WMS = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
LAYER = "Actueel_orthoHR"
MAX_PX = 1600
HEADERS = {"User-Agent": "zaanstad-dakscan/0.1 (interne tool)"}


def _padded(bounds, frac=0.15):
    minx, miny, maxx, maxy = bounds
    dx, dy = (maxx - minx) * frac, (maxy - miny) * frac
    p = max(dx, dy, 2.0)      # min 2 m marge
    return (minx - p, miny - p, maxx + p, maxy + p)


def _dims(bounds):
    minx, miny, maxx, maxy = bounds
    w_m, h_m = maxx - minx, maxy - miny
    if w_m >= h_m:
        W = MAX_PX; H = max(1, round(MAX_PX * h_m / w_m))
    else:
        H = MAX_PX; W = max(1, round(MAX_PX * w_m / h_m))
    return W, H


def _rd_to_px(bounds, W, H):
    """Maak een functie RD(x,y) -> pixel(px,py) voor deze crop."""
    minx, miny, maxx, maxy = bounds
    sx = W / (maxx - minx)
    sy = H / (maxy - miny)
    def f(x, y):
        return (round((x - minx) * sx), round((maxy - y) * sy))  # y omklappen
    return f


def _getmap(bounds, W, H):
    params = {
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
        "LAYERS": LAYER, "STYLES": "", "SRS": "EPSG:28992",
        "BBOX": ",".join(f"{b:.3f}" for b in bounds),   # 1.1.1: minx,miny,maxx,maxy
        "WIDTH": W, "HEIGHT": H, "FORMAT": "image/png",
    }
    r = requests.get(LUCHTFOTO_WMS, params=params, headers=HEADERS, timeout=45)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def _standin(W, H):
    """Geo-correcte stand-in (grijs membraan-achtig) om registratie te tonen."""
    img = Image.new("RGB", (W, H), (150, 150, 150))
    d = ImageDraw.Draw(img)
    for i in range(0, W + H, 22):                       # lichte textuur
        d.line([(i, 0), (i - H, H)], fill=(140, 140, 140), width=1)
    d.text((10, 10), "STAND-IN — echte luchtfoto komt van PDOK (Actueel_orthoHR)",
           fill=(60, 60, 60))
    return img


def haal(bounds, outfile, footprint=None, stand_in=False):
    b = _padded(bounds)
    W, H = _dims(b)
    img = _standin(W, H) if stand_in else _getmap(b, W, H)
    d = ImageDraw.Draw(img, "RGBA")
    to_px = _rd_to_px(b, W, H)

    # --- meetmodel eroverheen: footprint-omtrek ---
    if footprint is not None:
        geom = unary_union([footprint]) if not hasattr(footprint, "geoms") else footprint
        polys = list(getattr(geom, "geoms", [geom]))
        for p in polys:
            pts = [to_px(x, y) for (x, y) in p.exterior.coords]
            d.line(pts, fill=(255, 90, 40, 255), width=3)      # oranje omtrek

    # --- schaalbalk (5 m) ---
    minx, miny, maxx, maxy = b
    m_per_px = (maxx - minx) / W
    barpx = round(5 / m_per_px)
    x0, y0 = 14, H - 20
    d.line([(x0, y0), (x0 + barpx, y0)], fill=(255, 255, 255, 255), width=4)
    d.line([(x0, y0), (x0 + barpx, y0)], fill=(0, 0, 0, 255), width=2)
    d.text((x0, y0 - 14), "5 m", fill=(0, 0, 0, 255))

    img.save(outfile)
    return {"bestand": outfile, "bbox_rd": list(b), "layer": LAYER,
            "stand_in": stand_in}
