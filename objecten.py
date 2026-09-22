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

VERSION = "r4-2026-09-21"
