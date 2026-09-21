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

PROMPT = """Je bent dak-inspecteur. Dit is een LOODRECHTE luchtfoto van een PLAT dak.
De oranje lijn is de omtrek van het pand; kijk alleen BINNEN die omtrek.

Geef UITSLUITEND geldige JSON terug (geen uitleg, geen ```), met twee lijsten:

{
 "objecten": [
   {"type":"lichtstraat|lichtkoepel|schoorsteen|dakdoorvoer|installatie|dakraam|overig",
    "omschrijving":"kort","x_frac":0.0,"y_frac":0.0,"breedte_frac":0.0,"hoogte_frac":0.0,
    "zekerheid":"hoog|midden|laag"}
 ],
 "dakvlakken": [
   {"label":"A","omschrijving":"kort (bv. hoofdveld, lager aanbouwdak)",
    "x_frac":0.0,"y_frac":0.0,"breedte_frac":0.0,"hoogte_frac":0.0}
 ]
}

x_frac/y_frac = MIDDEN van het object als fractie 0..1 van de afbeelding
(x naar rechts, y naar beneden). breedte_frac/hoogte_frac = grootte als
fractie. Wees eerlijk met 'zekerheid'. Kleine doorvoeren mogen gemist worden."""


def _extract_json(txt):
    a, b = txt.find("{"), txt.rfind("}")
    if a == -1 or b == -1:
        return {"objecten": [], "dakvlakken": []}
    try:
        return json.loads(txt[a:b+1])
    except json.JSONDecodeError:
        return {"objecten": [], "dakvlakken": []}


def _frac_to_rd(o, bbox):
    """Zet fractie-coördinaten (0..1, y omlaag) om naar RD (x,y) + meters."""
    if not bbox:
        return None
    minx, miny, maxx, maxy = bbox
    w, h = maxx - minx, maxy - miny
    x = minx + float(o.get("x_frac", 0.5)) * w
    y = maxy - float(o.get("y_frac", 0.5)) * h          # y omklappen
    bw = float(o.get("breedte_frac", 0) or 0) * w
    bh = float(o.get("hoogte_frac", 0) or 0) * h
    return {"rd": (x, y), "grootte_m": (round(bw, 2), round(bh, 2))}


def analyse(image_path, bbox_rd=None):
    """Geef {objecten:[...], dakvlakken:[...], vision:status}."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"objecten": [], "dakvlakken": [], "vision": "uit (geen ANTHROPIC_API_KEY)"}

    data = base64.standard_b64encode(open(image_path, "rb").read()).decode()
    body = {"model": MODEL, "max_tokens": 1500, "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
        {"type": "text", "text": PROMPT}]}]}
    try:
        r = requests.post(API_URL, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"}, json=body, timeout=90)
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
        geo = _frac_to_rd(o, bbox_rd)
        objecten.append({"type": o.get("type", "overig"),
                         "omschrijving": o.get("omschrijving", ""),
                         "zekerheid": o.get("zekerheid", "laag"),
                         **(geo or {})})
    dakvlakken = []
    for d in js.get("dakvlakken", []):
        geo = _frac_to_rd(d, bbox_rd)
        dakvlakken.append({"label": d.get("label", "?"),
                           "omschrijving": d.get("omschrijving", ""),
                           **(geo or {})})
    return {"objecten": objecten, "dakvlakken": dakvlakken, "vision": "ok"}
