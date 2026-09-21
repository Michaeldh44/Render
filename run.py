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

    objecten_rd, vision_status = [], "luchtfoto uit"
    if args.luchtfoto:
        ov = f"{base}_ov.png"
        mlf = lf.haal(union.bounds, ov, footprint=union)
        if args.vision:
            res = objmod.analyse(ov, bbox_rd=mlf["bbox_rd"])
            objecten_rd = res.get("objecten", [])
            vision_status = res.get("vision")
            print(f"  vision: {vision_status} -> {len(objecten_rd)} objecten", file=sys.stderr)
        else:
            vision_status = "vision uit (verzoek)"

    paginas = bd.bouw_paginas(footprints, enrich=enrich, panddata=panddata,
                              meta=meta, objecten=objecten_rd, vision_status=vision_status)

    if args.luchtfoto:
        opstand = {"hoog": enrich.get("opstand_hoog_mm"),
                   "laag": enrich.get("opstand_laag_mm")}
        for i, p in enumerate(paginas):
            u = unary_union([f.buffer(0) for f in p["footprints"]])
            img = f"{base}_p{i}.png"
            mlf = lf.haal(u.bounds, img, footprint=u, objecten=p["objecten"],
                          opstand=opstand)
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
