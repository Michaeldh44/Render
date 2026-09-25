#!/usr/bin/env python3
"""
run.py  —  adres(sen)  ->  meerpagina-specblad (plat dak)
=========================================================
Voorbeeld (in JOUW omgeving, met PDOK-toegang + ANTHROPIC_API_KEY):

  python3 run.py "Zaandammerstraat 12, Zaandam" "Zaandammerstraat 14, Zaandam" \
                 --ref zd-000481-blok2 --out out/blok2.json --pdf out/blok2.pdf \
                 --luchtfoto --vision

Meerdere adressen -> panden samengevoegd; overzichtspagina + pagina per dakvlak.
"""
import argparse, json, sys
from shapely.ops import unary_union
import geo_sources as gs
import build_dakspec as bd
import luchtfoto as lf
import render
import objecten as objmod
import segment as segmod
import ahn
import dossier


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("adressen", nargs="+")
    ap.add_argument("--ref", default="zd-poc")
    ap.add_argument("--out", default="out/dakspec.json")
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--luchtfoto", action="store_true")
    ap.add_argument("--vision", action="store_true", help="Claude Vision (ANTHROPIC_API_KEY)")
    args = ap.parse_args()

    footprints, namen, pandids, panddata = [], [], [], {}
    for adres in args.adressen:
        fps, naam, pids, pdata = gs.footprints_for_address(adres)
        footprints += fps
        namen.append(naam)
        if not panddata:
            panddata = pdata
        for pid in pids:
            if pid not in pandids:
                pandids.append(pid)
        print(f"  {adres} -> pand(en): {pids or '(op kaartpositie)'}", file=sys.stderr)

    enrich = gs.dak_eigenschappen(pandids[0]) if pandids else {}
    titel = namen[0] + (f" e.a. ({len(namen)} adressen)" if len(namen) > 1 else "")
    meta = {"ref": args.ref, "adres": titel,
            "regel": f"{len(namen)} VHE / {len(pandids)} pand(en) = 1 dak"}
    union = unary_union([f.buffer(0) for f in footprints])
    base = args.out.rsplit(".", 1)[0]

    objecten_rd, vision_status, dakvlak_polys = [], "luchtfoto uit", None
    if args.luchtfoto:
        ov = f"{base}_ov.png"
        mlf = lf.haal(union.bounds, ov, footprint=union)
        contouren = segmod.segmenteer(ov, mlf["frame"], footprint=union)
        if args.vision:
            res = objmod.analyse(ov, frame=mlf["frame"])
            vision_status = res.get("vision")
            objecten_rd = (segmod.combineer(contouren, res.get("objecten", []))
                           if contouren else res.get("objecten", []))
            rects = bd.polys_from_vision(res.get("dakvlakken", []))
            if rects:
                dakvlak_polys = bd.split_dakvlakken(union, rects)
            ahn.meet_ruw(union, objecten_rd, bounds=union.bounds)   # hoogte vóór keuring
            k = objmod.keur(ov, objecten_rd, mlf["frame"])   # keurmeester (met hoogte)
            for i, o in enumerate(objecten_rd, 1):
                if i in k["oordeel"]:
                    o["keuring"] = k["oordeel"][i]
            objecten_rd += k["gemist"]
            afgekeurd = sum(1 for v in k["oordeel"].values() if v.get("echt") is False)
            print(f"  segmentatie: {len(contouren)} contouren | vision: {vision_status} | "
                  f"keuring: {k['keuring']} ({afgekeurd} afgekeurd, {len(k['gemist'])} gemist) | "
                  f"{len(dakvlak_polys or [])} dakvlakken", file=sys.stderr)
        else:
            objecten_rd = segmod.combineer(contouren, [])
            vision_status = "vision uit (verzoek)"

    # detecties in het DOSSIER; PDF wordt een view daarop
    objecten_rd = bd.dedup_objecten(objecten_rd)
    pandid = pandids[0] if pandids else f"geen-{args.ref}"
    dak_feiten = {"daktype": "plat" if enrich.get("is_plat", True) else "hellend",
                  "dakhoogte_m": enrich.get("dakhoogte_m"),
                  "opstand_hoog_mm": enrich.get("opstand_hoog_mm"),
                  "opstand_laag_mm": enrich.get("opstand_laag_mm")}
    _dos, view, meld = dossier.verwerk_run(pandid, objecten_rd, adres=titel,
                                           pand=panddata, dak=dak_feiten)
    print(f"  dossier {pandid}: {len(view)} object(en), {len(meld)} melding(en)",
          file=sys.stderr)

    afschot = None
    a = ahn.analyse(union, view, bounds=union.bounds)
    print(f"  AHN: {a['status']}", file=sys.stderr)
    if a["status"] == "ok":
        dossier.verwerk_ahn(_dos, a["metingen"])
        view = dossier.view_objecten(_dos)
        _dos["_view_order"] = [o["id"] for o in view]
        dossier.bewaar(_dos)
        meld = dossier.meldingen(_dos)
        afschot = a["afschot"]
        verhoogd = sum(1 for m in a["metingen"].values() if m.get("verhoogd"))
        afgek = sum(1 for m in a["metingen"].values() if m.get("afkeuren"))
        print(f"  AHN: {verhoogd} verhoogd, {afgek} afgekeurd | afschot {afschot}",
              file=sys.stderr)

    paginas = bd.bouw_paginas(footprints, enrich=enrich, panddata=panddata,
                              meta=meta, objecten=view,
                              vision_status=vision_status, dakvlakken=dakvlak_polys,
                              meldingen=meld, afschot=afschot)

    if args.luchtfoto:
        opstand = {"hoog": enrich.get("opstand_hoog_mm"),
                   "laag": enrich.get("opstand_laag_mm")}
        for i, p in enumerate(paginas):
            fp = p["footprint"]
            img = f"{base}_p{i}.png"
            mlf = lf.haal(fp.bounds, img, footprint=fp, objecten=p["objecten"],
                          opstand=opstand, label=p["label"], dakvlakken=p["dakvlakken"])
            p["spec"]["dakvisual"].update({"type": "image", "bestand": img,
                "onderschrift": f"PDOK-luchtfoto ({mlf['layer']}) met meet-omtrek"
                                + (" en objecten (Vision)" if p["objecten"] else "")})

    specs = [p["spec"] for p in paginas]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(specs, f, ensure_ascii=False, indent=2)
    print("JSON:", args.out, f"({len(specs)} pagina's)", file=sys.stderr)

    if args.pdf:
        render.render_specs(specs, args.pdf)


if __name__ == "__main__":
    main()

VERSION = "r6-2026-09-22"
