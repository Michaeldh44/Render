#!/usr/bin/env python3
"""
luchtfoto.py  —  DAKVISUAL uit een echte luchtfoto (PDOK)  [v0.3]
=================================================================
Haalt een loodrechte lucht-ortho (PDOK) op en RICHT 'm recht: het pand
staat horizontaal in beeld, met omhullende-maatlijnen die strak om het
pand vallen, meet-omtrek, objecten (Vision), noordpijl, schaalbalk en
opstand-aanduiding.

  haal(bounds, outfile, footprint=..., objecten=..., opstand=...) -> meta
  meta['frame'] = {o, du, dv}  -> RD van de beeldhoeken, zodat Vision-
  fracties correct naar RD vertaald worden (objecten.analyse).
"""
import io, math, requests
from PIL import Image, ImageDraw, ImageFont
from shapely.ops import unary_union

LUCHTFOTO_WMS = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
LAYER = "Actueel_orthoHR"
MAX_PX = 1800
HEADERS = {"User-Agent": "zaanstad-dakscan/0.3 (interne tool)"}
ORANJE = (255, 90, 40, 255)
BLAUW = (40, 108, 176, 255)
KADER = (40, 108, 176, 130)


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _padded(bounds, frac):
    minx, miny, maxx, maxy = bounds
    p = max((maxx - minx) * frac, (maxy - miny) * frac, 2.0)
    return (minx - p, miny - p, maxx + p, maxy + p)


def _dims(bounds):
    minx, miny, maxx, maxy = bounds
    w, h = maxx - minx, maxy - miny
    if w >= h:
        return MAX_PX, max(1, round(MAX_PX * h / w))
    return max(1, round(MAX_PX * w / h)), MAX_PX


def _label(d, xy, s, size=19, fg=(0, 0, 0, 255), pad=3):
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
    d.text((12, 12), "STAND-IN - echte luchtfoto komt van PDOK", fill=(60, 60, 60),
           font=_font(18))
    return img


def _getmap(bounds, W, H):
    params = {"SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
              "LAYERS": LAYER, "STYLES": "", "SRS": "EPSG:28992",
              "BBOX": ",".join(f"{b:.3f}" for b in bounds),
              "WIDTH": W, "HEIGHT": H, "FORMAT": "image/png"}
    r = requests.get(LUCHTFOTO_WMS, params=params, headers=HEADERS, timeout=45)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def _angle(geom):
    """Hoek (rad) van de langste zijde van de omhullende rechthoek."""
    c = list(geom.minimum_rotated_rectangle.exterior.coords)[:4]
    e0 = (c[1][0] - c[0][0], c[1][1] - c[0][1])
    e1 = (c[2][0] - c[1][0], c[2][1] - c[1][1])
    return (math.atan2(e0[1], e0[0]) if math.hypot(*e0) >= math.hypot(*e1)
            else math.atan2(e1[1], e1[0]))


def haal(bounds, outfile, footprint=None, objecten=None, opstand=None,
         label="A", dakvlakken=None, stand_in=False):
    b = _padded(bounds, 0.28)
    W0, H0 = _dims(b)
    img0 = _standin(W0, H0) if stand_in else _getmap(b, W0, H0)

    if footprint is None:
        img0.save(outfile)
        return {"bestand": outfile, "layer": LAYER, "stand_in": stand_in,
                "frame": {"o": (b[0], b[3]), "du": (b[2]-b[0], 0), "dv": (0, b[1]-b[3])}}

    geom = footprint if hasattr(footprint, "geoms") else unary_union([footprint])
    theta = _angle(geom)
    ct = geom.centroid
    Cx, Cy = ct.x, ct.y
    cs, sn = math.cos(theta), math.sin(theta)

    def uv(x, y):
        dx, dy = x - Cx, y - Cy
        return (cs*dx + sn*dy, -sn*dx + cs*dy)

    def rd(u, v):
        return (Cx + cs*u - sn*v, Cy + sn*u + cs*v)

    polys = list(getattr(geom, "geoms", [geom]))
    us = [uv(x, y) for p in polys for (x, y) in p.exterior.coords]
    umin = min(u for u, _ in us); umax = max(u for u, _ in us)
    vmin = min(v for _, v in us); vmax = max(v for _, v in us)
    padm = max(umax-umin, vmax-vmin) * 0.14
    Umin, Umax = umin-padm, umax+padm
    Vmin, Vmax = vmin-padm, vmax+padm
    res = MAX_PX / max(Umax-Umin, Vmax-Vmin)
    TW, TH = round((Umax-Umin)*res), round((Vmax-Vmin)*res)

    def TP(x, y):                      # RD -> doel-pixel (recht beeld)
        u, v = uv(x, y)
        return ((u-Umin)*res, (Vmax-v)*res)

    sx0, sy0 = W0/(b[2]-b[0]), H0/(b[3]-b[1])

    def src(tx, ty):                   # doel-pixel -> bron-pixel (noord-boven)
        u, v = Umin + tx/res, Vmax - ty/res
        x, y = rd(u, v)
        return ((x-b[0])*sx0, (b[3]-y)*sy0)

    s00, s10, s01 = src(0, 0), src(1, 0), src(0, 1)
    coeffs = (s10[0]-s00[0], s01[0]-s00[0], s00[0],
              s10[1]-s00[1], s01[1]-s00[1], s00[1])
    img = img0.transform((TW, TH), Image.AFFINE, coeffs,
                         resample=Image.BICUBIC, fillcolor=(150, 150, 150))
    d = ImageDraw.Draw(img, "RGBA")

    # meet-omtrek
    for p in polys:
        d.line([TP(x, y) for (x, y) in p.exterior.coords], fill=ORANJE, width=4)

    # deelvlakken (overzicht) of enkel dakvlak-label
    def _letter(L, x, y):
        px, py = TP(x, y)
        d.ellipse([px-18, py-18, px+18, py+18], fill=(255, 255, 255, 235),
                  outline=(0, 0, 0, 255), width=2)
        _label(d, (px-6, py-11), L, size=22)

    if dakvlakken:
        for L, poly in dakvlakken:
            for pp in getattr(poly, "geoms", [poly]):
                d.line([TP(x, y) for (x, y) in pp.exterior.coords],
                       fill=(40, 108, 176, 220), width=2)
            c = poly.representative_point()
            _letter(L, c.x, c.y)
    else:
        _letter(label, Cx, Cy)

    # omhullende-kader + maatlijnen (echte pandmaat = min-rect zijden)
    lengte, breedte = umax-umin, vmax-vmin
    tlx, tly = (umin-Umin)*res, (Vmax-vmax)*res
    trx = (umax-Umin)*res
    lby = (Vmax-vmin)*res
    for (xa, ya, xb, yb) in [(tlx, tly, trx, tly), (tlx, lby, trx, lby),
                             (tlx, tly, tlx, lby), (trx, tly, trx, lby)]:
        d.line([(xa, ya), (xb, yb)], fill=KADER, width=1)
    GAP = 22
    ty = tly - GAP
    d.line([(tlx, ty), (trx, ty)], fill=BLAUW, width=3)
    for xx in (tlx, trx):
        d.line([(xx, tly), (xx, ty)], fill=KADER, width=1)
        d.line([(xx, ty-6), (xx, ty+6)], fill=BLAUW, width=3)
    _label(d, ((tlx+trx)/2-62, ty-26), f"{lengte:.2f} m omhullend", size=19, fg=BLAUW)
    lx = tlx - GAP
    d.line([(lx, tly), (lx, lby)], fill=BLAUW, width=3)
    for yy in (tly, lby):
        d.line([(tlx, yy), (lx, yy)], fill=KADER, width=1)
        d.line([(lx-6, yy), (lx+6, yy)], fill=BLAUW, width=3)
    _label(d, (lx+8, (tly+lby)/2-10), f"{breedte:.2f} m", size=19, fg=BLAUW)

    # objecten (Vision)
    kleur = {"hoog": (47, 133, 90, 255), "midden": (183, 121, 31, 255),
             "laag": (192, 86, 33, 255)}
    for nr, o in enumerate(objecten or [], start=1):
        if not o.get("rd"):
            continue
        gw, gh = o.get("grootte_m") or (0.8, 0.8)
        px, py = TP(*o["rd"])
        hw, hh = max(7, gw*res/2), max(7, gh*res/2)
        col = kleur.get(o.get("zekerheid", "laag"), kleur["laag"])
        d.rectangle([px-hw, py-hh, px+hw, py+hh], outline=col, width=3,
                    fill=(255, 255, 255, 90))
        _label(d, (px-5, py-9), str(nr), size=16, fg=col, pad=2)

    # opstand-aanduiding
    if opstand and (opstand.get("hoog") or opstand.get("laag")):
        hoog, laag = opstand.get("hoog"), opstand.get("laag")
        txt = "opstand (3D BAG): "
        txt += f"hoog ~{hoog} mm" if hoog else ""
        txt += " \u00b7 " if hoog and laag is not None else ""
        txt += f"laag ~{laag} mm" if laag is not None else ""
        _label(d, (tlx, lby+10), txt, size=15, fg=(120, 60, 20, 255))

    # noordpijl (wijst schuin, want beeld is gedraaid)
    p0, pN = TP(Cx, Cy), TP(Cx, Cy+1)
    ndir = (pN[0]-p0[0], pN[1]-p0[1])
    mag = math.hypot(*ndir) or 1
    ux, uy = ndir[0]/mag, ndir[1]/mag
    nx, ny = TW-46, 50
    tx2, ty2 = nx+ux*26, ny+uy*26
    d.line([(nx, ny), (tx2, ty2)], fill=(0, 0, 0, 255), width=4)
    perp = (-uy, ux)
    d.polygon([(tx2, ty2),
               (tx2-ux*10+perp[0]*6, ty2-uy*10+perp[1]*6),
               (tx2-ux*10-perp[0]*6, ty2-uy*10-perp[1]*6)], fill=(0, 0, 0, 255))
    _label(d, (nx-6, ny-8), "N", size=16)

    # schaalbalk 5 m
    barpx = round(5 * res)
    x0, y0 = 16, TH-26
    d.line([(x0, y0), (x0+barpx, y0)], fill=(255, 255, 255, 255), width=6)
    d.line([(x0, y0), (x0+barpx, y0)], fill=(0, 0, 0, 255), width=3)
    _label(d, (x0, y0-24), "5 m", size=17)

    img.save(outfile)
    o_rd = rd(Umin, Vmax)
    du_rd = (rd(Umax, Vmax)[0]-o_rd[0], rd(Umax, Vmax)[1]-o_rd[1])
    dv_rd = (rd(Umin, Vmin)[0]-o_rd[0], rd(Umin, Vmin)[1]-o_rd[1])
    return {"bestand": outfile, "layer": LAYER, "stand_in": stand_in,
            "frame": {"o": o_rd, "du": du_rd, "dv": dv_rd}}
