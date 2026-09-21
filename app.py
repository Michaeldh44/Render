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

app = FastAPI(title="dakscan", version="0.1")


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
    formaat: str = "pdf"        # "pdf" of "json"


@app.post("/specblad")
def specblad(req: SpecbladReq):
    """Echt: adres(sen) -> BAG/3D BAG -> specblad. Heeft PDOK nodig."""
    try:
        pandids, namen = [], []
        for adres in req.adressen:
            ids, naam, _ = gs.geocode_to_pandids(adres)
            namen.append(naam)
            for pid in ids:
                if pid not in pandids:
                    pandids.append(pid)

        footprints, enrich = [], {}
        for i, pid in enumerate(pandids):
            footprints.append(gs.pand_footprint(pid))
            if i == 0:
                enrich = gs.dak_eigenschappen(pid)

        titel = namen[0] + (f" e.a. ({len(namen)} adressen)" if len(namen) > 1 else "")
        spec = bd.build(footprints, enrich=enrich,
                        meta={"ref": req.ref, "adres": titel,
                              "regel": f"{len(namen)} VHE / {len(pandids)} pand(en) = 1 dak"})

        if req.luchtfoto:
            union = unary_union([f.buffer(0) for f in footprints])
            img = f"/tmp/{req.ref}_luchtfoto.png"
            meta = lf.haal(union.bounds, img, footprint=union)   # echte PDOK-ortho
            spec["dakvisual"].update({
                "type": "image", "bestand": img,
                "onderschrift": f"DAKVISUAL — PDOK-luchtfoto ({meta['layer']}) met "
                                "meet-omtrek; illustratie, geen maatbron: meet in het DXF."})

        if req.formaat == "json":
            return spec
        return Response(render_pdf_bytes(spec), media_type="application/pdf")

    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:                                  # PDOK down / veldnaam?
        raise HTTPException(status_code=502, detail=f"open-data fout: {e}")
