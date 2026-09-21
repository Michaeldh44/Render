#!/usr/bin/env python3
"""
luchtfoto.py  —  DAKVISUAL uit een echte luchtfoto (PDOK)  [v0.2]
=================================================================
Haalt een loodrechte lucht-ortho (PDOK, ~8 cm) over de dak-bbox en tekent
er de VOLLEDIGE maatvoering op: meet-omtrek, omhullende-maten, noordpijl,
schaalbalk en dakvlak-label. Zo is de foto meteen de tekening.

  haal(bounds_rd, outfile, footprint=..., stand_in=False)

Illustratie, geen maatbron: meet in het DXF.
"""
import io, requests
from PIL import Image, ImageDraw, ImageFont
from shapely.ops import unary_union

LUCHTFOTO_WMS = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
LAYER = "Actueel_orthoHR"
MAX_PX = 1800
HEADERS = {"User-Agent": "zaanstad-dakscan/0.3 (interne tool)"}
ORANJE = (255, 90, 40, 255)
BLAUW  = (40, 108, 176, 255)


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _padded(bounds, frac=0.14):
    minx, miny, maxx, maxy = bounds
    p = max((maxx - minx) * frac, (maxy - miny) * frac, 2.0)
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
    minx, miny, maxx, maxy = bounds
    sx, sy = W / (maxx - minx), H / (maxy - miny)
    return lambda x, y: (round((x - minx) * sx), round((maxy - y) * sy))


def _label(d, xy, s, size=20, fg=(0, 0, 0, 255), pad=3):
    f = _font(size)
    x, y = xy
    l, t, r, b = d.textbbox((x, y), s, font=f)
    d.rectangle([l - pad, t - pad, r + pad, b + pad], fill=(255, 255, 255, 220))
    d.text((x, y), s, fill=fg, font=f)


def _standin(W, H):
    img = Image.new("RGB", (W, H), (150, 150, 150))
    d = ImageDraw.Draw(img)
    for i in range(0, W + H, 26):
        d.line([(i, 0), (i - H, H)], fill=(140, 140, 140), width=1)
    d.text((12, 12), "STAND-IN - echte luchtfoto komt van PDOK (Actueel_orthoHR)",
           fill=(60, 60, 60), font=_font(18))
    return img


def _getmap(bounds, W, H):
    params = {"SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
              "LAYERS": LAYER, "STYLES": "", "SRS": "EPSG:28992",
              "BBOX": ",".join(f"{b:.3f}" for b in bounds),
              "WIDTH": W, "HEIGHT": H, "FORMAT": "image/png"}
    r = requests.get(LUCHTFOTO_WMS, params=params, headers=HEADERS, timeout=45)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def haal(bounds, outfile, footprint=None, objecten=None, stand_in=False):
    b = _padded(bounds)
    W, H = _dims(b)
    img = _standin(W, H) if stand_in else _getmap(b, W, H)
    d = ImageDraw.Draw(img, "RGBA")
    to_px = _rd_to_px(b, W, H)

    if footprint is not None:
        geom = footprint if hasattr(footprint, "geoms") else unary_union([footprint])
        polys = list(getattr(geom, "geoms", [geom]))
        for p in polys:
            d.line([to_px(x, y) for (x, y) in p.exterior.coords],
                   fill=ORANJE, width=4)
        # dakvlak-label 'A' in centroid
        c = geom.centroid
        cx, cy = to_px(c.x, c.y)
        d.ellipse([cx-18, cy-18, cx+18, cy+18], fill=(255, 255, 255, 235),
                  outline=(0, 0, 0, 255), width=2)
        _label(d, (cx-6, cy-11), "A", size=22)

        # omhullende-maatlijnen langs de footprint-bbox
        fminx, fminy, fmaxx, fmaxy = geom.bounds
        lengte, breedte = fmaxx - fminx, fmaxy - fminy
        tlx, tly = to_px(fminx, fmaxy)
        trx, _ = to_px(fmaxx, fmaxy)
        ty = max(tly - 16, 10)
        d.line([(tlx, ty), (trx, ty)], fill=BLAUW, width=3)
        for xx in (tlx, trx):
            d.line([(xx, ty-6), (xx, ty+6)], fill=BLAUW, width=3)
        _label(d, ((tlx+trx)//2 - 60, ty-26), f"{lengte:.2f} m omhullend", size=19, fg=BLAUW)

        lx = max(tlx - 16, 10)
        _, lby = to_px(fminx, fminy)
        d.line([(lx, tly), (lx, lby)], fill=BLAUW, width=3)
        for yy in (tly, lby):
            d.line([(lx-6, yy), (lx+6, yy)], fill=BLAUW, width=3)
        _label(d, (lx+8, (tly+lby)//2 - 10), f"{breedte:.2f} m", size=19, fg=BLAUW)

    # objecten (Vision) als genummerde vakjes, kleur naar zekerheid
    kleur = {"hoog": (47, 133, 90, 255), "midden": (183, 121, 31, 255),
             "laag": (192, 86, 33, 255)}
    sx = W / (b[2] - b[0])
    sy = H / (b[3] - b[1])
    for nr, o in enumerate(objecten or [], start=1):
        rd = o.get("rd")
        if not rd:
            continue
        gw, gh = o.get("grootte_m") or (0.8, 0.8)
        px, py = to_px(*rd)
        hw = max(7, round(gw * sx / 2))
        hh = max(7, round(gh * sy / 2))
        col = kleur.get(o.get("zekerheid", "laag"), kleur["laag"])
        d.rectangle([px-hw, py-hh, px+hw, py+hh], outline=col, width=3,
                    fill=(255, 255, 255, 90))
        _label(d, (px-5, py-9), str(nr), size=16, fg=col, pad=2)

    # noordpijl
    nx, ny = W - 40, 46
    d.line([(nx, ny+18), (nx, ny-18)], fill=(0, 0, 0, 255), width=4)
    d.polygon([(nx-7, ny-9), (nx+7, ny-9), (nx, ny-20)], fill=(0, 0, 0, 255))
    _label(d, (nx-6, ny+22), "N", size=18)

    # schaalbalk 5 m
    minx, miny, maxx, maxy = b
    barpx = round(5 / ((maxx - minx) / W))
    x0, y0 = 16, H - 26
    d.line([(x0, y0), (x0+barpx, y0)], fill=(255, 255, 255, 255), width=6)
    d.line([(x0, y0), (x0+barpx, y0)], fill=(0, 0, 0, 255), width=3)
    _label(d, (x0, y0-24), "5 m", size=17)

    img.save(outfile)
    return {"bestand": outfile, "bbox_rd": list(b), "layer": LAYER, "stand_in": stand_in}
