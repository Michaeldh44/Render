#!/usr/bin/env python3
"""
app.py  —  dakscan als HTTP-service (voor in je portaal)
========================================================
Endpoints:
  GET  /health              -> {"status":"ok"}           (liveness)
  GET  /demo                 -> A3-PDF uit een ingebouwd voorbeeld
                                GEEN netwerk -> deploy-smoketest
  POST /specblad             -> A3-PDF (of JSON) uit echte adressen
                                body: {"adressen":[...], "ref":"...",
                                       "luchtfoto":true, "formaat":"pdf|json"}
                                heeft PDOK/3D BAG nodig (draait bij jou)

Sync endpoints -> FastAPI draait ze in een threadpool, zodat de
synchrone Playwright-render veilig kan (geen asyncio-conflict).
"""
import os
from fastapi import FastAPI, Response, HTTPException
from pydantic import BaseModel
from shapely.geometry import Polygon
from shapely.affinity import rotate, translate
from shapely.ops import unary_union
from playwright.sync_api import sync_playwright

import build_dakspec as bd
import geo_sources as gs
import luchtfoto as lf
import render as rnd
import objecten
import segment
import ahn
import dossier

app = FastAPI(title="dakscan", version="0.1")


@app.get("/")
def index():
    """Landingspagina: laat zien dat de service leeft + de endpoints."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(
        "<h2>dakscan — service draait ✔</h2>"
        "<ul>"
        "<li><a href='/health'>/health</a> — status</li>"
        "<li><a href='/test'>/test</a> — <b>invoerpagina in de browser</b> (adres → PDF)</li>"
        "<li><a href='/demo'>/demo</a> — voorbeeld-PDF (geen PDOK nodig)</li>"
        "<li><b>POST /specblad</b> — adres(sen) → PDF/JSON</li>"
        "</ul>")


def render_pdf_bytes(spec) -> bytes:
    html = rnd.build_html(spec)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.set_content(html, wait_until="networkidle")
        pdf = page.pdf(prefer_css_page_size=True, print_background=True)
        b.close()
    return pdf


@app.get("/health")
def health():
    return {"status": "ok", "service": "dakscan", "versie": "0.1"}


@app.get("/diag")
def diag(adres: str = "Roode Wildemanweg 45, Wormerveer"):
    """Diagnose: ziet de container de key, en wat geeft 3D BAG terug?"""
    import os, requests
    out = {"key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
           "vision_model": objecten.MODEL,
           "versies": {"geo_sources": getattr(gs, "VERSION", "?"),
                       "luchtfoto": getattr(lf, "VERSION", "?"),
                       "objecten": getattr(objecten, "VERSION", "?"),
                       "build_dakspec": getattr(bd, "VERSION", "?"),
                       "segment": getattr(segment, "VERSION", "?"),
                       "dossier": getattr(dossier, "VERSION", "?"),
                       "ahn": getattr(ahn, "VERSION", "?"),
                       "render": getattr(rnd, "VERSION", "?"),
                       "app": globals().get("VERSION", "?")}}
    if out["key_present"]:
        try:
            mh = {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                  "anthropic-version": "2023-06-01"}
            ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")
            if ws:
                mh["anthropic-workspace-id"] = ws
            mr = requests.get("https://api.anthropic.com/v1/models",
                              headers=mh, timeout=20)
            out["models_status"] = mr.status_code
            if mr.ok:
                out["models_available"] = [m.get("id") for m in mr.json().get("data", [])]
            else:
                out["models_body"] = mr.text[:300]
        except Exception as e:
            out["models_error"] = str(e)
    try:
        fps, naam, pids, pdata = gs.footprints_for_address(adres)
        out["adres"] = naam
        out["pandids"] = pids
        out["bouwjaar"] = pdata.get("bouwjaar")
        if pids:
            pid = pids[0]
            out["driedbag_tries"] = []
            for ident in (f"NL.IMBAG.Pand.{pid}", pid):
                r = requests.get(f"{gs.DRIEDBAG_API}/{ident}",
                                 headers=gs.HEADERS, timeout=30)
                out["driedbag_tries"].append({"ident": ident, "status": r.status_code})
                if r.status_code == 200:
                    data = r.json()
                    out["driedbag_toplevel_keys"] = list(data.keys())
                    cj = data.get("feature", data)
                    co = cj.get("CityObjects") or {}
                    for v in co.values():
                        a = v.get("attributes", {}) if isinstance(v, dict) else {}
                        if a:
                            out["driedbag_attribute_keys"] = sorted(a.keys())
                            break
                    out["driedbag_dak"] = gs.dak_eigenschappen(pid)
                    break
        # mini vision-test (alleen status, geen kosten als key ontbreekt)
        if fps:
            from shapely.ops import unary_union
            u = unary_union([f.buffer(0) for f in fps])
            img = "/tmp/diag_ov.png"
            m = lf.haal(u.bounds, img, footprint=u)
            out["vision_test"] = objecten.analyse(img, frame=m["frame"]).get("vision")
            # AHN-probe: haalt de container het hoogteraster op deze endpoint/laag?
            try:
                ai = ahn.haal_dsm(u.bounds)
                if ai and ai.get("grid") is not None:
                    g = ai["grid"]
                    import numpy as _np
                    geldig = g[~_np.isnan(g)]
                    out["ahn_test"] = {"status": "ok", "coverage": ahn.COVERAGE_DSM,
                                       "format": ai.get("format"),
                                       "raster": f"{ai['W']}x{ai['H']}",
                                       "nap_min": round(float(geldig.min()), 2) if geldig.size else None,
                                       "nap_max": round(float(geldig.max()), 2) if geldig.size else None}
                else:
                    out["ahn_test"] = {"status": ai.get("status") if ai else "geen data",
                                       "coverage": ahn.COVERAGE_DSM}
            except Exception as e:
                out["ahn_test"] = {"status": f"{type(e).__name__}: {e}"}
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


TEST_PAGE = """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dakscan — test</title><style>
 body{font-family:Helvetica,Arial,sans-serif;max-width:820px;margin:32px auto;padding:0 16px;color:#111}
 h1{font-size:20px;margin:0 0 4px} p.sub{color:#555;margin:0 0 20px}
 label{display:block;font-weight:700;margin:14px 0 4px}
 textarea,input{width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;font-size:14px;box-sizing:border-box}
 .row{display:flex;gap:16px;align-items:center;margin-top:12px}
 .row label{margin:0}
 button{margin-top:16px;background:#111;color:#fff;border:0;border-radius:6px;padding:11px 18px;font-size:15px;font-weight:700;cursor:pointer}
 button:disabled{opacity:.5;cursor:default}
 #status{margin-top:14px;font-size:14px}
 #err{color:#b00020;white-space:pre-wrap}
 iframe{width:100%;height:70vh;margin-top:16px;border:1px solid #ccc;border-radius:6px}
 a.dl{display:inline-block;margin-top:10px}
</style></head><body>
 <h1>dakscan — specblad testen</h1>
 <p class="sub">Eén adres per regel. Meerdere adressen = samengevoegd tot één dak.</p>
 <label for="adr">Adres(sen)</label>
 <textarea id="adr" rows="3" placeholder="Zaandammerstraat 12, Zaandam"></textarea>
 <div class="row">
   <div style="flex:1"><label for="ref">Referentie</label>
     <input id="ref" value="test"></div>
   <div><label><input type="checkbox" id="lf" checked> luchtfoto</label></div>
 </div>
 <button id="go" onclick="run()">Specblad maken</button>
 <div id="status"></div><div id="err"></div>
 <div id="out"></div>
<script>
async function run(){
  const btn=document.getElementById('go'), st=document.getElementById('status'),
        er=document.getElementById('err'), out=document.getElementById('out');
  er.textContent=''; out.innerHTML='';
  const adressen=document.getElementById('adr').value.split('\\n').map(s=>s.trim()).filter(Boolean);
  if(!adressen.length){er.textContent='Vul minstens één adres in.';return;}
  btn.disabled=true; st.textContent='Bezig… eerste keer kan 30–60 s duren (opstarten + PDOK).';
  try{
    const res=await fetch('/specblad',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({adressen,ref:document.getElementById('ref').value||'test',
                           luchtfoto:document.getElementById('lf').checked})});
    if(!res.ok){let d;try{d=(await res.json()).detail}catch(e){d=await res.text()}
      er.textContent='Fout '+res.status+': '+d; st.textContent=''; btn.disabled=false; return;}
    const url=URL.createObjectURL(await res.blob());
    st.textContent='Klaar.';
    out.innerHTML='<a class="dl" href="'+url+'" download="specblad.pdf">⬇ download PDF</a>'+
                  '<iframe src="'+url+'"></iframe>';
  }catch(e){er.textContent='Netwerkfout: '+e;st.textContent='';}
  btn.disabled=false;
}
</script></body></html>"""


@app.get("/test")
def test_page():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(TEST_PAGE)


@app.get("/demo")
def demo():
    """Voorbeeld-blok zonder netwerk: bewijst dat de container rendert."""
    base = Polygon([(0, 0), (27.6, 0), (27.6, 8.4), (0, 8.4)])
    foot = translate(rotate(base, 12, origin=(0, 0)), xoff=117500, yoff=498200)
    foot2 = translate(foot, xoff=27.6 * 0.9781, yoff=27.6 * 0.2079)
    union = unary_union([foot, foot2])
    spec = bd.build([foot, foot2],
                    enrich={"dakhoogte_m": 6.25, "dak_type": "horizontal",
                            "opp_plat": 231.8, "opp_schuin": 0},
                    meta={"ref": "demo", "adres": "DEMO — Zaandammerstraat 12 e.a.",
                          "regel": "2 panden = 1 dak"})
    img = "/tmp/demo_luchtfoto.png"
    lf.haal(union.bounds, img, footprint=union, stand_in=True)   # geen netwerk
    spec["dakvisual"].update({"type": "image", "bestand": img})
    return Response(render_pdf_bytes(spec), media_type="application/pdf")


class SpecbladReq(BaseModel):
    adressen: list[str]
    ref: str = "zd-poc"
    luchtfoto: bool = True
    vision: bool = True         # Claude Vision voor objecten + dakvlakken
    ahn: bool = True            # AHN-hoogtecheck (opsteek + afschot)
    formaat: str = "pdf"        # "pdf" of "json"


@app.post("/specblad")
def specblad(req: SpecbladReq):
    """Echt: adres(sen) -> BAG/3D BAG/luchtfoto/Vision -> meerpagina-specblad."""
    try:
        footprints, namen, pandids, panddata = [], [], [], {}
        for adres in req.adressen:
            fps, naam, pids, pdata = gs.footprints_for_address(adres)
            footprints += fps
            namen.append(naam)
            if not panddata:
                panddata = pdata
            for pid in pids:
                if pid not in pandids:
                    pandids.append(pid)
        enrich = gs.dak_eigenschappen(pandids[0]) if pandids else {}
        titel = namen[0] + (f" e.a. ({len(namen)} adressen)" if len(namen) > 1 else "")
        meta = {"ref": req.ref, "adres": titel,
                "regel": f"{len(namen)} VHE / {len(pandids)} pand(en) = 1 dak"}

        union = unary_union([f.buffer(0) for f in footprints])

        # 1) overzichtsfoto + segmentatie (contouren) + Vision (labels)
        objecten_rd, vision_status, dakvlak_polys = [], "luchtfoto uit", None
        if req.luchtfoto:
            ov_img = f"/tmp/{req.ref}_ov.png"
            meta_lf = lf.haal(union.bounds, ov_img, footprint=union)
            contouren = segment.segmenteer(ov_img, meta_lf["frame"], footprint=union)
            if req.vision:
                res = objecten.analyse(ov_img, frame=meta_lf["frame"])
                vision_status = res.get("vision")
                objecten_rd = (segment.combineer(contouren, res.get("objecten", []))
                               if contouren else res.get("objecten", []))
                rects = bd.polys_from_vision(res.get("dakvlakken", []))
                if rects:
                    dakvlak_polys = bd.split_dakvlakken(union, rects)
                # AHN-opsteek meten VÓÓR de keuring, zodat de inspecteur met hoogte oordeelt
                ahn.meet_ruw(union, objecten_rd, bounds=union.bounds)
                # KEURMEESTER: Vision beoordeelt de genummerde objecten (met hoogte-context)
                k = objecten.keur(ov_img, objecten_rd, meta_lf["frame"])
                for i, o in enumerate(objecten_rd, 1):
                    if i in k["oordeel"]:
                        o["keuring"] = k["oordeel"][i]
                objecten_rd += k["gemist"]                 # gemiste objecten (maatklasse C)
                vision_status = f"{vision_status} | keuring: {k['keuring']}"
            else:
                objecten_rd = segment.combineer(contouren, [])
                vision_status = "vision uit (verzoek)"

        # verwijder overbodige verzamelvakken (scheve box over losse panelen e.d.)
        objecten_rd = bd.dedup_objecten(objecten_rd)

        # 2) detecties in het DOSSIER schrijven; PDF wordt een view daarop
        pandid = pandids[0] if pandids else f"geen-{req.ref}"
        dak_feiten = {"daktype": "plat" if enrich.get("is_plat", True) else "hellend",
                      "dakhoogte_m": enrich.get("dakhoogte_m"),
                      "opstand_hoog_mm": enrich.get("opstand_hoog_mm"),
                      "opstand_laag_mm": enrich.get("opstand_laag_mm")}
        _dos, view, meld = dossier.verwerk_run(pandid, objecten_rd, adres=titel,
                                               pand=panddata, dak=dak_feiten)

        # 2b) AHN-hoogtecheck: onafhankelijke bron (opsteek per object + afschot)
        afschot = None
        ahn_status = "uit"
        if req.ahn:
            a = ahn.analyse(union, view, bounds=union.bounds)
            ahn_status = a["status"]
            if a["status"] == "ok":
                dossier.verwerk_ahn(_dos, a["metingen"])
                view = dossier.view_objecten(_dos)
                _dos["_view_order"] = [o["id"] for o in view]
                dossier.bewaar(_dos)
                meld = dossier.meldingen(_dos)
                afschot = a["afschot"]
                dak_feiten["afschot"] = afschot
                dak_feiten["ahn_basis_nap"] = a["basis_nap"]
                _dos["dak"] = dak_feiten
                dossier.bewaar(_dos)

        # 3) meerpagina-opbouw uit de dossier-view
        paginas = bd.bouw_paginas(footprints, enrich=enrich, panddata=panddata,
                                  meta=meta, objecten=view,
                                  vision_status=vision_status, dakvlakken=dakvlak_polys,
                                  meldingen=meld, afschot=afschot)

        # 3) per pagina de luchtfoto met objecten/omtrek
        if req.luchtfoto:
            opstand = {"hoog": enrich.get("opstand_hoog_mm"),
                       "laag": enrich.get("opstand_laag_mm")}
            for i, p in enumerate(paginas):
                fp = p["footprint"]
                img = f"/tmp/{req.ref}_p{i}.png"
                mlf = lf.haal(fp.bounds, img, footprint=fp, objecten=p["objecten"],
                              opstand=opstand, label=p["label"], dakvlakken=p["dakvlakken"])
                p["spec"]["dakvisual"].update({
                    "type": "image", "bestand": img,
                    "onderschrift": f"PDOK-luchtfoto ({mlf['layer']}) met meet-omtrek"
                                    + (" en objecten (Vision)" if p["objecten"] else "")})

        # bewaar de genummerde overzichtsfoto voor het review-scherm
        if req.luchtfoto and paginas:
            import shutil
            src = f"/tmp/{req.ref}_p0.png"
            if os.path.exists(src):
                beeld = f"/tmp/review_{pandid}.png"
                shutil.copyfile(src, beeld)
                _dos["_beeld"] = beeld
                dossier.bewaar(_dos)

        specs = [p["spec"] for p in paginas]
        if req.formaat == "json":
            return specs
        return Response(rnd.render_specs_bytes(specs), media_type="application/pdf")

    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"open-data/vision fout: {e}")

class OordeelReq(BaseModel):
    pandid: str
    nummer: int | None = None      # nummer [n] van de laatste specblad
    obj_id: str | None = None      # of direct het object-id
    actie: str = "weg"             # "weg" | "behouden" | "type"
    waarde: str | None = None      # bij actie "type": het nieuwe type


@app.post("/oordeel")
def oordeel(req: OordeelReq):
    """Mens-in-de-lus: keur een object af, behoud het, of herlabel het.
    Blijft plakken over volgende runs heen."""
    r = dossier.registreer_oordeel(req.pandid, nummer=req.nummer, obj_id=req.obj_id,
                                   actie=req.actie, waarde=req.waarde)
    if not r:
        raise HTTPException(status_code=404, detail="dak of object niet gevonden")
    return r


@app.get("/beeld")
def beeld(pandid: str):
    """De genummerde overzichtsfoto van het laatste specblad (voor /review)."""
    from fastapi.responses import FileResponse
    dos = dossier.laad(pandid)
    pad = (dos or {}).get("_beeld")
    if not pad or not os.path.exists(pad):
        raise HTTPException(status_code=404, detail="geen beeld (draai eerst een specblad)")
    return FileResponse(pad, media_type="image/png")


TYPES = ["zonnepaneel", "lichtstraat", "lichtkoepel", "installatie",
         "schoorsteen", "dakdoorvoer", "dakraam", "overig"]


def _review_html(pandid, dos):
    view = dossier.view_objecten(dos)                       # actieve objecten
    allen = dossier.view_objecten(dos, alleen_actief=False)
    nazien = [o for o in allen if o["status"] in ("betwijfeld", "verdwenen")]
    adres = dos.get("adres", pandid)
    heeft_beeld = bool(dos.get("_beeld") and os.path.exists(dos["_beeld"]))

    rijen = []
    for n, o in enumerate(view, 1):
        opts = "".join(f'<option value="{t}"{" selected" if t==o["type"] else ""}>{t}</option>'
                       for t in TYPES)
        m2 = f'{o["m2"]:.1f} m²' if o.get("m2") else ""
        rijen.append(f"""
        <tr id="r-{o['id']}">
          <td class="nr">{n}</td>
          <td>{o['type']} <span class="mk mk{o['zekerheid']}">{o['zekerheid']}</span></td>
          <td class="m2">{m2}</td>
          <td>
            <select onchange="wijzig('{o['id']}', this.value)">{opts}</select>
            <button class="weg" onclick="oordeel('{o['id']}','weg')">weg</button>
          </td>
        </tr>""")

    nazien_html = ""
    if nazien:
        nr = []
        for o in nazien:
            reden = "verdwenen sinds vorige scan" if o["status"] == "verdwenen" \
                else "Vision betwijfelt dit object"
            nr.append(f"""
            <tr id="r-{o['id']}">
              <td>{o['type']} · <span class="dim">{reden}</span></td>
              <td><button onclick="oordeel('{o['id']}','behouden')">toch behouden</button></td>
            </tr>""")
        nazien_html = f"""
        <h2>Te controleren ({len(nazien)})</h2>
        <table class="nazien">{''.join(nr)}</table>"""

    beeld_html = (f'<img src="/beeld?pandid={pandid}" alt="dakoverzicht">'
                  if heeft_beeld else
                  '<p class="dim">Geen overzichtsfoto beschikbaar — draai eerst een specblad '
                  'voor dit pand.</p>')

    return f"""<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dakscan review · {adres}</title>
<style>
  body{{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f6f7f8;color:#1a1a1a}}
  header{{background:#1a1a1a;color:#fff;padding:14px 20px}}
  header b{{font-weight:600}} header .sub{{opacity:.7;font-size:13px}}
  .wrap{{max-width:1100px;margin:0 auto;padding:20px;display:grid;
        grid-template-columns:1fr 1fr;gap:24px}}
  @media(max-width:800px){{.wrap{{grid-template-columns:1fr}}}}
  img{{width:100%;border:1px solid #ddd;border-radius:6px}}
  h2{{font-size:15px;margin:18px 0 8px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e3e3e3;
        border-radius:6px;overflow:hidden}}
  td{{padding:8px 10px;border-bottom:1px solid #f0f0f0;vertical-align:middle}}
  .nr{{color:#888;width:28px}} .m2{{color:#555;white-space:nowrap;width:70px}}
  select{{padding:4px;margin-right:6px}}
  button{{padding:5px 10px;border:1px solid #bbb;background:#fff;border-radius:5px;cursor:pointer}}
  button.weg{{border-color:#c0392b;color:#c0392b}}
  button:hover{{background:#f0f0f0}}
  .dim{{color:#999}} .mk{{font-size:11px;padding:1px 5px;border-radius:3px;color:#fff}}
  .mkA{{background:#2e7d32}} .mkB{{background:#e08600}} .mkC{{background:#888}}
  .flash{{opacity:.45;transition:opacity .3s}}
</style></head><body>
<header><b>dakscan · review</b> &nbsp; <span class="sub">{adres} · {pandid}</span></header>
<div class="wrap">
  <div>{beeld_html}</div>
  <div>
    <h2>Objecten op dak ({len(view)})</h2>
    <table><tbody>{''.join(rijen)}</tbody></table>
    {nazien_html}
    <p class="dim" style="margin-top:14px">Klik <b>weg</b> om een fout-object te schrappen,
    of kies een ander type. Je correctie blijft plakken bij de volgende scan.</p>
  </div>
</div>
<script>
const PAND = {pandid!r};
async function post(body){{
  const r = await fetch('/oordeel',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify(body)}});
  return r.ok;
}}
async function oordeel(id, actie){{
  const row=document.getElementById('r-'+id); if(row) row.classList.add('flash');
  if(await post({{pandid:PAND,obj_id:id,actie}})) location.reload();
  else alert('opslaan mislukt');
}}
async function wijzig(id, type){{
  if(await post({{pandid:PAND,obj_id:id,actie:'type',waarde:type}})) location.reload();
  else alert('opslaan mislukt');
}}
</script></body></html>"""


@app.get("/review")
def review(pandid: str):
    """Mens-in-de-lus review-scherm: schrap of herlabel objecten met één klik."""
    from fastapi.responses import HTMLResponse
    dos = dossier.laad(pandid)
    if not dos:
        raise HTTPException(status_code=404, detail="geen dossier voor dit pand")
    return HTMLResponse(_review_html(pandid, dos))


VERSION = "r6-2026-09-22"
