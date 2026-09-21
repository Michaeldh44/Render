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
           "vision_model": objecten.MODEL}
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
            out["vision_test"] = objecten.analyse(img, bbox_rd=m["bbox_rd"]).get("vision")
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

        # 1) overzichtsfoto (voor Vision) + Vision-analyse
        objecten_rd, vision_status = [], "luchtfoto uit"
        if req.luchtfoto:
            ov_img = f"/tmp/{req.ref}_ov.png"
            meta_lf = lf.haal(union.bounds, ov_img, footprint=union)
            if req.vision:
                res = objecten.analyse(ov_img, bbox_rd=meta_lf["bbox_rd"])
                objecten_rd = res.get("objecten", [])
                vision_status = res.get("vision")
            else:
                vision_status = "vision uit (verzoek)"

        # 2) meerpagina-opbouw (overzicht + per dakvlak)
        paginas = bd.bouw_paginas(footprints, enrich=enrich, panddata=panddata,
                                  meta=meta, objecten=objecten_rd, vision_status=vision_status)

        # 3) per pagina de luchtfoto met objecten/omtrek
        if req.luchtfoto:
            opstand = {"hoog": enrich.get("opstand_hoog_mm"),
                       "laag": enrich.get("opstand_laag_mm")}
            for i, p in enumerate(paginas):
                u = unary_union([f.buffer(0) for f in p["footprints"]])
                img = f"/tmp/{req.ref}_p{i}.png"
                mlf = lf.haal(u.bounds, img, footprint=u, objecten=p["objecten"],
                              opstand=opstand)
                p["spec"]["dakvisual"].update({
                    "type": "image", "bestand": img,
                    "onderschrift": f"PDOK-luchtfoto ({mlf['layer']}) met meet-omtrek"
                                    + (" en objecten (Vision)" if p["objecten"] else "")})

        specs = [p["spec"] for p in paginas]
        if req.formaat == "json":
            return specs
        return Response(rnd.render_specs_bytes(specs), media_type="application/pdf")

    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"open-data/vision fout: {e}")
