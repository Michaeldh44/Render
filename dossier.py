#!/usr/bin/env python3
"""
dossier.py  —  HET DAKDOSSIER (objectmodel met claims)  [stap 1]
================================================================
Eén bestand per dak: <pandid>.json. De PDF is een VIEW hierop, niet de bron.

Kern:
- Elk object heeft een STABIEL id (overleeft runs) + geometrie + een lijst
  CLAIMS. Een claim = één observatie van één bron, met confidence + tijd.
- Bronnen: segmentatie (geometrie), vision-label (type), vision-keuring
  (oordeel), ahn (hoogte - GERESERVEERD), gebruiker (wint altijd).
- De status/type/zekerheid van een object worden AFGELEID uit de claims
  (resolve), niet los opgeslagen. Nieuwe bron toevoegen = nieuwe claim-soort,
  geen herbouw.

Merge-regel bij een nieuwe run (afgesproken):
- nieuwe GEOMETRIE wint (maatvast uit de foto),
- jouw OORDEEL blijft plakken zolang het object op dezelfde plek ligt,
- verdwijnt een object, dan wordt dat GEMELD (niet stil weggegooid).

Opslag is nu file-per-dak; dezelfde structuur gaat later 1-op-1 naar
Supabase (pandid = primary key). Let op: op een ephemeral filesystem
(zoals Render zonder disk) overleven bestanden een redeploy niet - dan is
een persistente disk of Supabase nodig.
"""
import json
import os
import time
from shapely.geometry import shape, Polygon

VERSION = "r6-2026-09-22"
DATA_DIR = os.environ.get("DAKSCAN_DATA_DIR", "data/daken")
AUTO_BRONNEN = {"segmentatie", "vision-label", "vision-keuring"}   # vervangbaar per run
BLIJVEND = {"gebruiker", "ahn"}                                    # blijft over runs heen
IOU_MATCH = 0.30


# ---------- opslag --------------------------------------------------------
def _pad(pandid):
    return os.path.join(DATA_DIR, f"{pandid}.json")


def laad(pandid):
    p = _pad(pandid)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def bewaar(dossier):
    os.makedirs(DATA_DIR, exist_ok=True)
    dossier["bijgewerkt"] = _nu()
    with open(_pad(dossier["pandid"]), "w", encoding="utf-8") as f:
        json.dump(dossier, f, ensure_ascii=False, indent=2)
    return _pad(dossier["pandid"])


def nieuw(pandid, adres="", pand=None, dak=None):
    return {"pandid": pandid, "adres": adres, "aangemaakt": _nu(),
            "bijgewerkt": _nu(), "pand": pand or {}, "dak": dak or {},
            "objecten": [], "_teller": 0}


def _nu():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------- claims --------------------------------------------------------
def claim(bron, soort, waarde, confidence, notitie=None):
    c = {"bron": bron, "soort": soort, "waarde": waarde,
         "confidence": round(float(confidence), 3), "ts": _nu()}
    if notitie:
        c["notitie"] = notitie
    return c


def voeg_claim_toe(dossier, obj_id, c):
    """Voeg een claim toe (bv. vision-keuring, AHN-hoogte, of jouw oordeel).
    Een gebruiker- of ahn-claim vervangt een eerdere van dezelfde bron+soort."""
    for o in dossier["objecten"]:
        if o["id"] == obj_id:
            if c["bron"] in BLIJVEND:
                o["claims"] = [x for x in o["claims"]
                               if not (x["bron"] == c["bron"] and x["soort"] == c["soort"])]
            o["claims"].append(c)
            return True
    return False


# ---------- merge van een nieuwe detectie-run -----------------------------
def _poly(obj):
    try:
        return Polygon(obj["geometrie"]["poly_rd"]).buffer(0)
    except Exception:
        return None


def _iou(a, b):
    if a is None or b is None or a.is_empty or b.is_empty:
        return 0.0
    inter = a.intersection(b).area
    uni = a.union(b).area
    return inter / uni if uni else 0.0


def verwerk_detecties(dossier, detecties):
    """detecties: list van {poly_rd, m2, claims:[...]} (segmentatie-geometrie
    + auto-bron-claims). Merge in het dossier volgens de merge-regel."""
    bestaand = [o for o in dossier["objecten"] if o.get("status") != "afgekeurd"]
    det_polys = [Polygon(d["poly_rd"]).buffer(0) for d in detecties]
    gekoppeld = {}                       # det-index -> bestaand object
    gebruikt_best = set()

    for di, dp in enumerate(det_polys):
        best_i, best_iou = None, IOU_MATCH
        for oi, o in enumerate(bestaand):
            if oi in gebruikt_best:
                continue
            iou = _iou(dp, _poly(o))
            if iou > best_iou:
                best_i, best_iou = oi, iou
        if best_i is not None:
            gekoppeld[di] = bestaand[best_i]
            gebruikt_best.add(best_i)

    for di, det in enumerate(detecties):
        obj = gekoppeld.get(di)
        if obj is None:                              # nieuw object
            dossier["_teller"] += 1
            obj = {"id": f"obj-{dossier['_teller']:04d}", "claims": [],
                   "status": "actief"}
            dossier["objecten"].append(obj)
        # nieuwe geometrie wint
        obj["geometrie"] = {"poly_rd": det["poly_rd"], "m2": det.get("m2")}
        obj.pop("verdwenen", None)
        obj["status"] = "actief" if not _afgekeurd_door_gebruiker(obj) else "afgekeurd"
        # auto-claims verversen, blijvende (gebruiker/ahn) behouden
        obj["claims"] = [c for c in obj["claims"] if c["bron"] in BLIJVEND]
        obj["claims"] += det.get("claims", [])

    # bestaande actieve objecten zonder match -> verdwenen (gemeld, niet weg).
    # Een door jou afgekeurd object blijft afgekeurd - nooit "verdwenen".
    for oi, o in enumerate(bestaand):
        if (oi not in gebruikt_best and o.get("status") == "actief"
                and not _afgekeurd_door_gebruiker(o)):
            o["verdwenen"] = True
            o["status"] = "verdwenen"
    return dossier


def _afgekeurd_door_gebruiker(obj):
    return any(c["bron"] == "gebruiker" and c["soort"] == "oordeel"
               and c["waarde"] in ("weg", "afgekeurd") for c in obj["claims"])


# ---------- resolve: van claims naar afgeleide waarheid -------------------
def resolve(obj):
    claims = obj["claims"]
    geo = obj.get("geometrie", {})

    def laatste(bron, soort):
        xs = [c for c in claims if c["bron"] == bron and c["soort"] == soort]
        return xs[-1] if xs else None

    # status
    if _afgekeurd_door_gebruiker(obj):
        status = "afgekeurd"
    elif obj.get("status") == "verdwenen":
        status = "verdwenen"
    else:
        keuring = laatste("vision-keuring", "oordeel")
        gebr_keep = any(c["bron"] == "gebruiker" and c["soort"] == "oordeel"
                        and c["waarde"] == "behouden" for c in claims)
        if keuring and keuring["waarde"] in ("geen object", "afkeuren") and not gebr_keep:
            status = "betwijfeld"          # Vision keurt af -> uit de lijst, maar wel gemeld
        else:
            status = "actief"

    # type: gebruiker wint, anders hoogste-confidence type-claim
    gebr_type = laatste("gebruiker", "type")
    if gebr_type:
        typ, typ_bron = gebr_type["waarde"], "gebruiker"
    else:
        types = [c for c in claims if c["soort"] == "type"]
        best = max(types, key=lambda c: c["confidence"]) if types else None
        typ = best["waarde"] if best else "overig"
        typ_bron = best["bron"] if best else None

    # zekerheid: bronnen die stemmen
    score = 0.0
    if geo.get("poly_rd"):
        score += 0.4                                   # maatvaste geometrie
    if laatste("vision-label", "type"):
        score += 0.3                                   # type bevestigd door foto
    if laatste("ahn", "hoogte"):
        score += 0.2                                   # onafhankelijke hoogte-bron
    kc = laatste("vision-keuring", "oordeel")
    if kc and kc["waarde"] in ("behouden", "echt", "bevestigd"):
        score += 0.2                                   # inspecteur bevestigt: echt object
    if gebr_type or any(c["bron"] == "gebruiker" and c["waarde"] == "behouden"
                        for c in claims if c["soort"] == "oordeel"):
        score = 1.0                                    # jij hebt bevestigd
    zekerheid = "A" if score >= 0.75 else "B" if score >= 0.5 else "C"

    hoogte = laatste("ahn", "hoogte")
    rd = None
    if geo.get("poly_rd"):
        try:
            rd = tuple(Polygon(geo["poly_rd"]).centroid.coords[0])
        except Exception:
            rd = None
    return {"id": obj["id"], "status": status, "type": typ, "type_bron": typ_bron,
            "zekerheid": zekerheid, "score": round(score, 2),
            "m2": geo.get("m2"), "poly_rd": geo.get("poly_rd"), "rd": rd,
            "hoogte": hoogte["waarde"] if hoogte else None,
            "verdwenen": obj.get("status") == "verdwenen"}


def view_objecten(dossier, alleen_actief=True):
    """De lijst die de PDF-builder gebruikt: afgeleide objecten uit claims."""
    out = []
    for o in dossier["objecten"]:
        r = resolve(o)
        if alleen_actief and r["status"] != "actief":
            continue
        out.append(r)
    return out


def meldingen(dossier):
    """Wat de mens moet nakijken: objecten die verdwenen zijn, of die Vision als
    keurmeester heeft afgekeurd (betwijfeld) - met de reden erbij."""
    m = []
    for o in dossier["objecten"]:
        r = resolve(o)
        if r["status"] == "verdwenen":
            m.append(f"{r['id']}: eerder gezien, nu niet gedetecteerd - nakijken")
        elif r["status"] == "betwijfeld":
            reden = ""
            for c in o["claims"]:
                if c["bron"] == "vision-keuring" and c["soort"] == "oordeel" and c.get("notitie"):
                    reden = f" ({c['notitie']})"
            m.append(f"{r['id']}: Vision beoordeelt dit als geen echt object{reden} - "
                     f"niet meegeteld, controleer")
    return m


def detecties_uit_objecten(objecten_rd):
    """Zet de pijplijn-objecten (segmentatie-contouren + losse Vision-blokken)
    om naar dossier-detecties met claims. Contour = sterke geometrie;
    los Vision-blok = zwakke geometrie (klein vierkant om het punt)."""
    conf = {"hoog": 0.7, "midden": 0.5, "laag": 0.3}
    dets = []
    for o in objecten_rd:
        if o.get("poly_rd"):
            poly, m2 = o["poly_rd"], o.get("m2")
            geo_bron, geo_conf = "segmentatie", 0.9
        elif o.get("rd"):
            x, y = o["rd"]
            gw, gh = o.get("grootte_m") or (0.6, 0.6)
            poly = [[x-gw/2, y-gh/2], [x+gw/2, y-gh/2],
                    [x+gw/2, y+gh/2], [x-gw/2, y+gh/2]]
            m2 = round(gw*gh, 2)
            geo_bron, geo_conf = "vision-label", 0.4
        else:
            continue
        claims = [claim(geo_bron, "geometrie", {"m2": m2}, geo_conf),
                  claim("vision-label", "type", o.get("type", "overig"),
                        conf.get(o.get("zekerheid", "laag"), 0.3))]
        k = o.get("keuring")
        if k:                                   # de keurmeester heeft dit object beoordeeld
            z = k.get("zekerheid", "midden")
            kconf = {"hoog": 0.9, "midden": 0.7, "laag": 0.5}.get(z, 0.7)
            if k.get("echt") is False:
                claims.append(claim("vision-keuring", "oordeel", "afkeuren", kconf,
                                    k.get("reden")))
            else:
                claims.append(claim("vision-keuring", "oordeel", "behouden", kconf,
                                    k.get("reden")))
                if k.get("type"):               # inspecteur-label overrulet het losse label
                    tconf = {"hoog": 0.85, "midden": 0.65, "laag": 0.45}.get(z, 0.65)
                    claims.append(claim("vision-keuring", "type", k["type"], tconf))
        dets.append({"poly_rd": poly, "m2": m2, "claims": claims})
    return dets


def verwerk_run(pandid, objecten_rd, adres="", pand=None, dak=None):
    """Volledige stap: laad/maak dossier, merge de detecties, bewaar, en geef
    (dossier, view-objecten, meldingen) terug. De PDF rendert uit de view."""
    dos = laad(pandid) or nieuw(pandid, adres=adres)
    if pand:
        dos["pand"] = pand
    if dak:
        dos["dak"] = dak
    verwerk_detecties(dos, detecties_uit_objecten(objecten_rd))
    view = view_objecten(dos)
    dos["_view_order"] = [o["id"] for o in view]
    bewaar(dos)
    return dos, view, meldingen(dos)


def registreer_oordeel(pandid, nummer=None, obj_id=None, actie="behouden", waarde=None):
    """Mens-in-de-lus: keur af / herlabel / behoud. Blijft plakken over runs."""
    dos = laad(pandid)
    if not dos:
        return None
    if obj_id is None and nummer is not None:
        order = dos.get("_view_order", [])
        if 1 <= nummer <= len(order):
            obj_id = order[nummer-1]
    if not obj_id:
        return None
    if actie in ("weg", "afgekeurd"):
        voeg_claim_toe(dos, obj_id, claim("gebruiker", "oordeel", "weg", 1.0))
    elif actie == "behouden":
        voeg_claim_toe(dos, obj_id, claim("gebruiker", "oordeel", "behouden", 1.0))
    elif actie == "type" and waarde:
        voeg_claim_toe(dos, obj_id, claim("gebruiker", "type", waarde, 1.0))
    view = view_objecten(dos)
    dos["_view_order"] = [o["id"] for o in view]
    bewaar(dos)
    return {"obj_id": obj_id, "actie": actie, "waarde": waarde,
            "objecten": len(view)}


VERSION = "r6-2026-09-22"
