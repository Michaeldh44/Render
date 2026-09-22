#!/usr/bin/env python3
"""
objecten.py  —  Claude Vision op de luchtfoto  [v0.1]
=====================================================
Identificeert dak-objecten (lichtstraat, koepel, schoorsteen, doorvoer,
installatie, dakraam) EN wijst de dakvlakken aan op de loodrechte
luchtfoto. Geeft gestructureerde JSON terug, omgerekend naar RD zodat de
pipeline de objecten op de tekening kan zetten.

Model + beeldformaat volgens platform.claude.com/docs (base64 image).
Key uit omgeving: ANTHROPIC_API_KEY  (NOOIT in de repo zetten).
Geen key -> geeft lege lijsten terug; de rest van het blad werkt gewoon.
"""
import os, json, base64, requests

API_URL = "https://api.anthropic.com/v1/messages"
# actuele Vision-modellen (docs): claude-sonnet-4-5 (bewezen), claude-sonnet-5, claude-opus-5.
MODEL = os.environ.get("DAKSCAN_VISION_MODEL", "claude-sonnet-4-5")

PROMPT = """Je bent een ervaren dakinspecteur. Dit is een LOODRECHTE luchtfoto (~8 cm/pixel)
van een PLAT dak. De ORANJE lijn is de omtrek van het pand; kijk ALLEEN binnen die omtrek
en analyseer het dak zorgvuldig en VOLLEDIG.

Herken en benoem ELK opvallend object. Gebruik deze types en aanwijzingen:
- zonnepaneel : DONKERE (zwart/donkerblauw) rechthoekige panelen, meestal in RIJEN/velden.
  Groepeer een aaneengesloten veld als EEN object; schat in 'omschrijving' het aantal
  panelen of rijen. Dit is vaak het grootste dakoppervlak - mis het NIET en verwar het
  niet met een installatie.
- lichtstraat : LICHTE/witte langwerpige rechthoeken (daglichtstraten).
- lichtkoepel : kleine lichte koepels (vierkant/rond).
- installatie : technische units zoals luchtbehandeling/koeling, grijze kasten - GEEN panelen.
- schoorsteen : opgemetselde of ronde schoorstenen.
- dakdoorvoer : kleine ronde ontluchtingen/doorvoeren.
- dakraam / overig.

Bepaal ook de DAKVLAKKEN: duidelijk van elkaar gescheiden dakdelen, met NAME lagere
aanbouwen/uitbouwen aan een zijde (bv. een lager voordak of bijgebouw-dak). Geef per
dakvlak de rechthoekige begrenzing, ook als het hoogteverschil subtiel is.

Geef UITSLUITEND geldige JSON (geen uitleg, geen ```):
{
 "objecten": [
   {"type":"zonnepaneel|lichtstraat|lichtkoepel|installatie|schoorsteen|dakdoorvoer|dakraam|overig",
    "omschrijving":"kort, incl. geschat aantal bij panelen",
    "x_frac":0.0,"y_frac":0.0,"breedte_frac":0.0,"hoogte_frac":0.0,
    "zekerheid":"hoog|midden|laag"}
 ],
 "dakvlakken": [
   {"label":"A","omschrijving":"hoofdvlak / lagere uitbouw links / ...",
    "x_frac":0.0,"y_frac":0.0,"breedte_frac":0.0,"hoogte_frac":0.0}
 ]
}

x_frac/y_frac = MIDDEN van het object als fractie 0..1 van de afbeelding (x naar rechts,
y naar beneden). breedte_frac/hoogte_frac = grootte als fractie. Wees nauwkeurig en
volledig; noem liever een object te veel dan te weinig."""


def _extract_json(txt):
    a, b = txt.find("{"), txt.rfind("}")
    if a == -1 or b == -1:
        return {"objecten": [], "dakvlakken": []}
    try:
        return json.loads(txt[a:b+1])
    except json.JSONDecodeError:
        return {"objecten": [], "dakvlakken": []}


def _fp_to_rd(xf, yf, frame):
    ox, oy = frame["o"]; dux, duy = frame["du"]; dvx, dvy = frame["dv"]
    return (ox + xf*dux + yf*dvx, oy + xf*duy + yf*dvy)


def _frac_to_rd(o, frame):
    """Zet fractie-coördinaten (0..1 van het beeld) om naar RD via het
    beeld-frame {o, du, dv} = RD van de beeldhoeken."""
    if not frame:
        return None
    import math
    dux, duy = frame["du"]; dvx, dvy = frame["dv"]
    x, y = _fp_to_rd(float(o.get("x_frac", 0.5)), float(o.get("y_frac", 0.5)), frame)
    bw = float(o.get("breedte_frac", 0) or 0) * math.hypot(dux, duy)
    bh = float(o.get("hoogte_frac", 0) or 0) * math.hypot(dvx, dvy)
    return {"rd": (x, y), "grootte_m": (round(bw, 2), round(bh, 2))}


def analyse(image_path, frame=None):
    """Geef {objecten:[...], dakvlakken:[...], vision:status}."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"objecten": [], "dakvlakken": [], "vision": "uit (geen ANTHROPIC_API_KEY)"}

    data = base64.standard_b64encode(open(image_path, "rb").read()).decode()
    body = {"model": MODEL, "max_tokens": 3000, "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
        {"type": "text", "text": PROMPT}]}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")   # nodig bij een org-key
    if ws:
        headers["anthropic-workspace-id"] = ws
    try:
        r = requests.post(API_URL, headers=headers, json=body, timeout=90)
    except requests.RequestException as e:
        return {"objecten": [], "dakvlakken": [], "vision": f"netwerkfout: {e}"}
    if r.status_code != 200:
        # toon de ECHTE reden (modelnaam, formaat, ...) i.p.v. alleen de status
        return {"objecten": [], "dakvlakken": [],
                "vision": f"fout {r.status_code}: {r.text[:300]}"}

    txt = "".join(b.get("text", "") for b in r.json().get("content", [])
                  if b.get("type") == "text")
    js = _extract_json(txt)

    objecten = []
    for o in js.get("objecten", []):
        geo = _frac_to_rd(o, frame)
        objecten.append({"type": o.get("type", "overig"),
                         "omschrijving": o.get("omschrijving", ""),
                         "zekerheid": o.get("zekerheid", "laag"),
                         **(geo or {})})
    dakvlakken = []
    for d in js.get("dakvlakken", []):
        geo = _frac_to_rd(d, frame)
        poly_rd = None
        if frame:
            xf, yf = float(d.get("x_frac", 0.5)), float(d.get("y_frac", 0.5))
            wf = float(d.get("breedte_frac", 0) or 0) / 2
            hf = float(d.get("hoogte_frac", 0) or 0) / 2
            poly_rd = [_fp_to_rd(xf-wf, yf-hf, frame), _fp_to_rd(xf+wf, yf-hf, frame),
                       _fp_to_rd(xf+wf, yf+hf, frame), _fp_to_rd(xf-wf, yf+hf, frame)]
        dakvlakken.append({"label": d.get("label", "?"),
                           "omschrijving": d.get("omschrijving", ""),
                           "poly_rd": poly_rd, **(geo or {})})
    return {"objecten": objecten, "dakvlakken": dakvlakken, "vision": "ok"}


KEUR_PROMPT = """Je bent een ervaren dakinspecteur die een automatische dakscan CONTROLEERT.
Dit is een loodrechte luchtfoto van een PLAT dak. Een segmentatie-algoritme heeft
kandidaat-objecten OMLIJND en met een ROOD NUMMER gemarkeerd.

Beoordeel als inspecteur ELK genummerd object kritisch:
- "echt": is dit een ECHT dakobject, of is het gewoon dakhuid / schaduw / een vlek / een
  reflectie? Wees streng: een "lichtstraat" of "paneel" dat een groot deel van het dak
  beslaat is vrijwel altijd dakhuid (echt=false).
- "type": wat is het WERKELIJK? (zonnepaneel, lichtstraat, lichtkoepel, installatie,
  schoorsteen, dakdoorvoer, dakraam, overig)
- "zekerheid": hoog | midden | laag
- "reden": heel kort waarom (bv. "dakhuid, geen object" of "duidelijk zonnepaneelveld").

Noem daarnaast objecten die je DUIDELIJK ziet maar die NIET genummerd zijn ("gemist").

Geef UITSLUITEND geldige JSON (geen uitleg, geen ```):
{
 "oordeel":[{"nr":1,"echt":true,"type":"zonnepaneel","zekerheid":"hoog","reden":"kort"}],
 "gemist":[{"type":"dakdoorvoer","x_frac":0.0,"y_frac":0.0,"breedte_frac":0.0,"hoogte_frac":0.0,"reden":"kort"}]
}"""


def _rd_to_px(x, y, W, H, frame):
    ox, oy = frame["o"]; dux, duy = frame["du"]; dvx, dvy = frame["dv"]
    det = dux*dvy - duy*dvx
    if abs(det) < 1e-9:
        return (0, 0)
    bx, by = x - ox, y - oy
    xf = (bx*dvy - by*dvx) / det
    yf = (dux*by - duy*bx) / det
    return (int(xf*W), int(yf*H))


def _teken_nummers(image_path, objs, frame):
    """Teken het volgnummer van elk object op een kopie van de ortho, zodat de
    keurmeester per nummer kan oordelen. Geeft het pad van de controle-afbeelding."""
    import cv2
    img = cv2.imread(image_path)
    if img is None:
        return None
    H, W = img.shape[:2]
    for i, o in enumerate(objs, 1):
        if not o.get("rd"):
            continue
        px, py = _rd_to_px(o["rd"][0], o["rd"][1], W, H, frame)
        if o.get("poly_rd"):
            pts = [_rd_to_px(x, y, W, H, frame) for (x, y) in o["poly_rd"]]
            import numpy as np
            cv2.polylines(img, [np.array(pts, np.int32)], True, (0, 90, 255), 2)
        cv2.putText(img, str(i), (px-8, py+6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(img, str(i), (px-8, py+6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 0, 255), 2, cv2.LINE_AA)
    out = image_path.rsplit(".", 1)[0] + "_keur.png"
    cv2.imwrite(out, img)
    return out


def keur(image_path, objs, frame):
    """Vision als KEURMEESTER: beoordeelt de genummerde segmentatie-objecten.
    Geeft {"oordeel": {nr: {echt,type,zekerheid,reden}}, "gemist": [...],
    "keuring": status}. Mag alleen schrappen/herlabelen - geen geometrie tekenen.
    Faalt veilig: zonder key of bij een fout blijft de rest van de scan werken."""
    leeg = {"oordeel": {}, "gemist": []}
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {**leeg, "keuring": "uit (geen ANTHROPIC_API_KEY)"}
    if not objs:
        return {**leeg, "keuring": "geen objecten om te keuren"}
    ctrl = _teken_nummers(image_path, objs, frame)
    if not ctrl:
        return {**leeg, "keuring": "controle-afbeelding mislukt"}

    data = base64.standard_b64encode(open(ctrl, "rb").read()).decode()
    body = {"model": MODEL, "max_tokens": 3000, "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
        {"type": "text", "text": KEUR_PROMPT}]}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    if ws:
        headers["anthropic-workspace-id"] = ws
    try:
        r = requests.post(API_URL, headers=headers, json=body, timeout=90)
    except requests.RequestException as e:
        return {**leeg, "keuring": f"netwerkfout: {e}"}
    if r.status_code != 200:
        return {**leeg, "keuring": f"fout {r.status_code}: {r.text[:300]}"}

    txt = "".join(b.get("text", "") for b in r.json().get("content", [])
                  if b.get("type") == "text")
    a, b = txt.find("{"), txt.rfind("}")
    try:
        js = json.loads(txt[a:b+1]) if a != -1 else {}
    except json.JSONDecodeError:
        return {**leeg, "keuring": "kon keuring-JSON niet lezen"}

    oordeel = {}
    for v in js.get("oordeel", []):
        try:
            nr = int(v.get("nr"))
        except (TypeError, ValueError):
            continue
        oordeel[nr] = {"echt": v.get("echt", True), "type": v.get("type"),
                       "zekerheid": v.get("zekerheid", "midden"),
                       "reden": v.get("reden", "")}
    gemist = []
    for g in js.get("gemist", []):
        geo = _frac_to_rd(g, frame) or {}
        gemist.append({"type": g.get("type", "overig"),
                       "omschrijving": g.get("reden", "door keurmeester gezien"),
                       "zekerheid": "laag", **geo})
    return {"oordeel": oordeel, "gemist": gemist, "keuring": "ok"}


VERSION = "r6-2026-09-22"
