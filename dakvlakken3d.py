#!/usr/bin/env python3
"""
dakvlakken3d.py  —  DAKVLAKKEN UIT 3D BAG LoD 2.2  [v2]
=======================================================
Vervangt de Vision-rechthoeken. 3D BAG (TU Delft) levert per pand een
LoD 2.2-model met de echte dakvlakken (RoofSurface) inclusief hoogte.
Daaruit halen we, zonder AI en dus herhaalbaar:

  - dakvlakken (polygoon RD, m² plat en werkelijk, hoogte NAP, helling, afschot)
  - per dakvlak de randen, ingedeeld naar wat er in de calculatie gebeurt:
      dakrand        vrije rand (gevel) -> randafwerking
      rand_lager     rand boven een LAGER dakvlak (ook dakrand, maar met valhoogte)
      opgaand        rand tegen een HOGER dakvlak van hetzelfde pand -> opgaand werk
      opgaand_buur   rand tegen een HOGER buurpand -> opgaand werk
      grens_buur     rand tegen een buurpand van gelijke/lagere hoogte (scheidingsmuur)
      naad           naad tussen twee dakvlakken op gelijke hoogte (geen detail)

  haal(pandids, footprint) -> {"status", "dakvlakken":[...], "buren":n}

Geen netwerk of geen LoD 2.2 -> status != "ok" en lege lijst; de scan valt
dan terug op de BAG-footprint als één dakvlak.
"""
import math
import requests
from shapely.geometry import Polygon, LineString, Point, shape
from shapely.ops import unary_union

DRIEDBAG_API = "https://api.3dbag.nl/collections/pand/items"
HEADERS = {"User-Agent": "zaanstad-dakscan/2.0 (interne tool)"}
TIMEOUT = 30

PLAT_MAX_GRADEN = 5.0       # helling < 5 graden = plat dak (met afschot)
SPRONG_M = 0.30             # hoogteverschil >= 30 cm = echte sprong (opgaand / rand)
BUUR_SPRONG_M = 0.50        # buurpand >= 50 cm hoger = opgaand werk tegen buurpand
NAAD_TOL_M = 0.35           # rand ligt 'op' een ander vlak/buur binnen deze afstand
MIN_VLAK_M2 = 2.0           # kleinere snippers worden bij de buur gevoegd
AFSCHOT_MIN_M2 = 25.0      # kleiner vlak: geen afschot rapporteren
MIN_SEG_M = 0.25            # randsegmenten korter dan dit negeren


# ---------------------------------------------------------------- 3D BAG ophalen
def _get_item(pandid):
    for ident in (f"NL.IMBAG.Pand.{pandid}", pandid):
        try:
            r = requests.get(f"{DRIEDBAG_API}/{ident}", headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            continue
    return None


def _get_bbox(bounds, marge=1.5):
    minx, miny, maxx, maxy = bounds
    try:
        r = requests.get(DRIEDBAG_API, headers=HEADERS, timeout=TIMEOUT,
                         params={"bbox": f"{minx-marge},{miny-marge},{maxx+marge},{maxy+marge}",
                                 "limit": 50})
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


def _transform(item, feat):
    t = ((item or {}).get("metadata") or {}).get("transform") \
        or ((feat or {}).get("metadata") or {}).get("transform") \
        or {"scale": [1, 1, 1], "translate": [0, 0, 0]}
    return t["scale"], t["translate"]


def _verts(feat, scale, translate):
    return [(v[0]*scale[0]+translate[0], v[1]*scale[1]+translate[1], v[2]*scale[2]+translate[2])
            for v in feat.get("vertices", [])]


def roofsurfaces_uit_feature(item):
    """CityJSONFeature (3D BAG-item) -> lijst van dakvlak-ringen [(exterior3d, [holes3d])].
    Neemt de hoogste beschikbare LoD (2.2 > 1.3 > 1.2)."""
    feat = item.get("feature", item)
    scale, translate = _transform(item, feat)
    V = _verts(feat, scale, translate)
    out = []
    for oid, obj in (feat.get("CityObjects") or {}).items():
        geoms = obj.get("geometry") or []
        if not geoms:
            continue
        best = None
        for g in geoms:
            lod = str(g.get("lod", "0"))
            if g.get("type") not in ("Solid", "MultiSurface", "CompositeSurface"):
                continue
            if best is None or lod > str(best.get("lod")):
                best = g
        if not best or str(best.get("lod")) < "1.2":
            continue
        sem = best.get("semantics") or {}
        types = [s.get("type") for s in sem.get("surfaces", [])]
        if best["type"] == "Solid":
            shells, vals = best["boundaries"], sem.get("values") or []
        else:
            shells, vals = [best["boundaries"]], [sem.get("values") or []]
        for si, shell in enumerate(shells):
            for k, surf in enumerate(shell):
                try:
                    ti = vals[si][k]
                except (IndexError, TypeError):
                    continue
                if ti is None or types[ti] != "RoofSurface":
                    continue
                rings = [[V[i] for i in ring] for ring in surf]
                out.append((rings[0], rings[1:], str(best.get("lod"))))
    return out


# ---------------------------------------------------------------- geometrie
def _vlak_fit(pts3d):
    """Kleinste-kwadratenvlak z = a*x + b*y + c op de hoekpunten."""
    n = len(pts3d)
    if n < 3:
        return 0.0, 0.0, (pts3d[0][2] if pts3d else 0.0)
    x0 = sum(p[0] for p in pts3d)/n; y0 = sum(p[1] for p in pts3d)/n
    sxx = sum((p[0]-x0)**2 for p in pts3d); syy = sum((p[1]-y0)**2 for p in pts3d)
    sxy = sum((p[0]-x0)*(p[1]-y0) for p in pts3d)
    z0 = sum(p[2] for p in pts3d)/n
    sxz = sum((p[0]-x0)*(p[2]-z0) for p in pts3d); syz = sum((p[1]-y0)*(p[2]-z0) for p in pts3d)
    det = sxx*syy - sxy*sxy
    if abs(det) < 1e-9:
        return 0.0, 0.0, z0
    a = (sxz*syy - syz*sxy)/det
    b = (syz*sxx - sxz*sxy)/det
    c = z0 - a*x0 - b*y0
    return a, b, c


def _z_op(fit, x, y):
    a, b, c = fit
    return a*x + b*y + c


def _poly2d(ext, holes):
    try:
        p = Polygon([(x, y) for x, y, _ in ext], [[(x, y) for x, y, _ in h] for h in holes]).buffer(0)
    except Exception:
        return None
    if p.is_empty:
        return None
    if p.geom_type != "Polygon":
        p = max(getattr(p, "geoms", [p]), key=lambda g: g.area)
    return p


def _segmenten(poly):
    """Alle randsegmenten: buitenrand én binnenranden (gaten, bv. rond een liftopbouw)."""
    for r in [poly.exterior] + list(poly.interiors):
        ring = list(r.coords)
        for (ax, ay), (bx, by) in zip(ring, ring[1:]):
            L = math.hypot(bx-ax, by-ay)
            if L >= MIN_SEG_M:
                yield (ax, ay), (bx, by), L


def _deel_segment(a, b, L, stap=1.0):
    """Lange segmenten in stukken, zodat een rand die half tegen een hoger deel
    ligt en half vrij is, goed verdeeld wordt."""
    n = max(1, int(math.ceil(L / stap)))
    for i in range(n):
        t0, t1 = i/n, (i+1)/n
        p0 = (a[0]+(b[0]-a[0])*t0, a[1]+(b[1]-a[1])*t0)
        p1 = (a[0]+(b[0]-a[0])*t1, a[1]+(b[1]-a[1])*t1)
        yield p0, p1, L/n


def _naar_buiten(poly, mx, my, dx, dy, afstand):
    """Punt op `afstand` meter loodrecht op het segment, aan de buitenkant van poly."""
    nx, ny = -dy, dx
    mag = math.hypot(nx, ny) or 1.0
    nx, ny = nx/mag, ny/mag
    if poly.contains(Point(mx + nx*0.05, my + ny*0.05)):
        nx, ny = -nx, -ny
    return mx + nx*afstand, my + ny*afstand


# ---------------------------------------------------------------- hoofdfunctie
def bouw_dakvlakken(roof_rings, buren=None):
    """roof_rings: [(ext3d, holes3d, lod)]; buren: [{"poly":Polygon, "h_dak":float}]
    -> lijst dakvlakken (gesorteerd op oppervlak, groot eerst) met randen."""
    vlakken = []
    for ext, holes, lod in roof_rings:
        p = _poly2d(ext, holes)
        if p is None or p.area < 0.05:
            continue
        pts = list(ext) + [q for h in holes for q in h]
        fit = _vlak_fit(pts)
        a, b, _ = fit
        grad = math.hypot(a, b)                         # m per m
        helling = math.degrees(math.atan(grad))
        zs = [q[2] for q in pts]
        vlakken.append({"poly": p, "fit": fit, "helling_deg": helling, "grad": grad,
                        "h_gem": sum(zs)/len(zs), "h_min": min(zs), "h_max": max(zs), "lod": lod})

    # snippers bij de aangrenzende buur met de dichtstbijzijnde hoogte voegen
    groot = [v for v in vlakken if v["poly"].area >= MIN_VLAK_M2]
    klein = [v for v in vlakken if v["poly"].area < MIN_VLAK_M2]
    for k in klein:
        kand = [g for g in groot if g["poly"].distance(k["poly"]) < 0.1]
        if kand:
            g = min(kand, key=lambda g: abs(g["h_gem"] - k["h_gem"]))
            g["poly"] = unary_union([g["poly"], k["poly"]]).buffer(0)
            if g["poly"].geom_type != "Polygon":
                g["poly"] = max(g["poly"].geoms, key=lambda q: q.area)
    vlakken = groot or vlakken
    vlakken.sort(key=lambda v: v["poly"].area, reverse=True)

    buren = buren or []
    for i, v in enumerate(vlakken):
        v["label"] = chr(65 + i) if i < 26 else f"V{i+1}"

    for v in vlakken:
        randen = {"dakrand": 0.0, "rand_lager": 0.0, "opgaand": 0.0,
                  "opgaand_buur": 0.0, "grens_buur": 0.0, "naad": 0.0}
        sprongen = []
        P = v["poly"]
        for a, b, L in _segmenten(P):
            for p0, p1, l in _deel_segment(a, b, L):
                mx, my = (p0[0]+p1[0])/2, (p0[1]+p1[1])/2
                z_eigen = _z_op(v["fit"], mx, my)
                ox, oy = _naar_buiten(P, mx, my, p1[0]-p0[0], p1[1]-p0[1], NAAD_TOL_M)
                buiten = Point(ox, oy)
                # 1) ander dakvlak van hetzelfde pand?
                ander = None
                for w in vlakken:
                    if w is v:
                        continue
                    if w["poly"].buffer(0.05).contains(buiten) or w["poly"].distance(buiten) < 0.05:
                        ander = w
                        break
                if ander is not None:
                    dz = _z_op(ander["fit"], mx, my) - z_eigen
                    if dz >= SPRONG_M:
                        randen["opgaand"] += l; sprongen.append(round(dz, 2))
                    elif dz <= -SPRONG_M:
                        randen["rand_lager"] += l
                    else:
                        randen["naad"] += l
                    continue
                # 2) buurpand?
                buur = None
                for bu in buren:
                    if bu["poly"].distance(buiten) < 0.15:
                        buur = bu
                        break
                if buur is not None and buur.get("h_dak") is not None:
                    if buur["h_dak"] - z_eigen >= BUUR_SPRONG_M:
                        randen["opgaand_buur"] += l
                    else:
                        randen["grens_buur"] += l
                    continue
                randen["dakrand"] += l
        plat = v["helling_deg"] < PLAT_MAX_GRADEN
        m2_plat = P.area
        m2_werkelijk = m2_plat / math.cos(math.radians(v["helling_deg"])) if not plat else m2_plat
        v.update({
            "plat": plat,
            "m2_plat": round(m2_plat, 2),
            "m2": round(m2_werkelijk, 2),
            "hoogte_nap": round(v["h_gem"], 2),
            # afschot alleen bij voldoende groot vlak: op een klein vlak is de fit onbetrouwbaar
            "afschot_pct": round(v["grad"]*100, 2) if (plat and m2_plat >= AFSCHOT_MIN_M2) else None,
            "afschot_mm_per_m": round(v["grad"]*1000, 1) if (plat and m2_plat >= AFSCHOT_MIN_M2) else None,
            "helling_deg": round(v["helling_deg"], 1),
            "randen": {k: round(x, 1) for k, x in randen.items()},
            "max_sprong_m": max(sprongen) if sprongen else None,
        })
    return vlakken


def buren_uit_bbox(js, eigen_ids):
    """3D BAG bbox-respons -> buurpanden [{poly, h_dak}] (eigen panden eruit)."""
    out = []
    if not js:
        return out
    for f in js.get("features", []):
        feat = f.get("feature", f) if isinstance(f, dict) else {}
        meta_item = {"metadata": js.get("metadata") or f.get("metadata")}
        scale, translate = _transform(meta_item, feat)
        V = _verts(feat, scale, translate)
        for oid, obj in (feat.get("CityObjects") or {}).items():
            if obj.get("type") != "Building":
                continue
            ident = oid.replace("NL.IMBAG.Pand.", "")
            if ident in eigen_ids or oid in eigen_ids:
                continue
            attrs = obj.get("attributes") or {}
            h = attrs.get("b3_h_dak_50p") or attrs.get("b3_h_dak_70p")
            poly = None
            for g in obj.get("geometry") or []:
                if str(g.get("lod")) == "0":
                    try:
                        ring = g["boundaries"][0][0]
                        poly = Polygon([(V[i][0], V[i][1]) for i in ring]).buffer(0)
                    except Exception:
                        poly = None
            if poly is None:
                # val terug op de voetafdruk van het LoD1.2-dak
                rr = roofsurfaces_uit_feature({"feature": {"CityObjects": {oid: obj},
                                                           "vertices": feat.get("vertices", [])},
                                               **meta_item})
                ps = [_poly2d(e, hl) for e, hl, _ in rr]
                ps = [p for p in ps if p is not None]
                poly = unary_union(ps) if ps else None
            if poly is not None and not poly.is_empty:
                out.append({"poly": poly, "h_dak": float(h) if h is not None else None, "id": ident})
    return out


def haal(pandids, footprint=None):
    """Haal LoD 2.2-dakvlakken voor de opgegeven panden (+ buren voor randtypering)."""
    rings = []
    for pid in pandids or []:
        item = _get_item(pid)
        if item:
            rings += roofsurfaces_uit_feature(item)
    if not rings:
        return {"status": "geen 3D BAG LoD 2.2", "dakvlakken": [], "buren": 0}
    bounds = footprint.bounds if footprint is not None else \
        unary_union([_poly2d(e, h) for e, h, _ in rings if _poly2d(e, h)]).bounds
    eigen = set(str(p) for p in pandids)
    buren = buren_uit_bbox(_get_bbox(bounds), eigen)
    vlakken = bouw_dakvlakken(rings, buren)
    lods = sorted(set(v["lod"] for v in vlakken))
    return {"status": "ok", "dakvlakken": vlakken, "buren": len(buren), "lod": "/".join(lods)}


def samenvatting(vlakken):
    """Totalen over alle dakvlakken (voor dakgegevens/raming)."""
    t = {"m2": 0.0, "dakrand": 0.0, "rand_lager": 0.0, "opgaand": 0.0,
         "opgaand_buur": 0.0, "grens_buur": 0.0}
    for v in vlakken:
        t["m2"] += v["m2"]
        for k in ("dakrand", "rand_lager", "opgaand", "opgaand_buur", "grens_buur"):
            t[k] += v["randen"][k]
    return {k: round(x, 1) for k, x in t.items()}


VERSION = "v2-2026-10-19"
