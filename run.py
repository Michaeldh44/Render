#!/usr/bin/env python3
"""
run.py  —  adres(sen)  ->  specblad (plat dak)
==============================================
Voorbeeld (in JOUW omgeving, met PDOK-toegang):

  python3 run.py "Zaandammerstraat 12, Zaandam" \
                 "Zaandammerstraat 14, Zaandam" \
                 --ref zd-000481-blok2 --out out/blok2.json --pdf out/blok2.pdf

Meerdere adressen -> panden worden samengevoegd tot één dak.
"""
import argparse, json, sys
import geo_sources as gs
import build_dakspec as bd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("adressen", nargs="+", help="één of meer adressen (blok)")
    ap.add_argument("--ref", default="zd-poc")
    ap.add_argument("--out", default="out/dakspec.json")
    ap.add_argument("--pdf", default=None, help="ook direct een A3-PDF renderen")
    ap.add_argument("--luchtfoto", action="store_true",
                    help="echte PDOK-luchtfoto als DAKVISUAL ophalen")
    args = ap.parse_args()

    footprints, namen, pandids = [], [], []
    for adres in args.adressen:
        fps, naam, pids = gs.footprints_for_address(adres)
        footprints += fps
        namen.append(naam)
        for pid in pids:
            if pid not in pandids:
                pandids.append(pid)
        print(f"  {adres}  ->  pand(en): {pids or '(op kaartpositie)'}", file=sys.stderr)

    enrich = gs.dak_eigenschappen(pandids[0]) if pandids else {}

    titel = namen[0] + (f" e.a. ({len(namen)} adressen)" if len(namen) > 1 else "")
    spec = bd.build(footprints, enrich=enrich,
                    meta={"ref": args.ref, "adres": titel,
                          "regel": f"{len(namen)} VHE / {len(pandids)} pand(en) = 1 dak"})

    if args.luchtfoto:
        import luchtfoto
        from shapely.ops import unary_union
        union = unary_union([f.buffer(0) for f in footprints])
        img_pad = args.out.rsplit(".", 1)[0] + "_luchtfoto.png"
        meta = luchtfoto.haal(union.bounds, img_pad, footprint=union)  # stand_in=False bij jou
        spec["dakvisual"]["type"] = "image"
        spec["dakvisual"]["bestand"] = img_pad
        spec["dakvisual"]["onderschrift"] = (
            f"DAKVISUAL — PDOK-luchtfoto ({meta['layer']}) met meet-omtrek eroverheen; "
            "illustratie, geen maatbron: meet in het DXF.")
        print("luchtfoto:", img_pad, file=sys.stderr)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    print("JSON:", args.out, file=sys.stderr)

    if args.pdf:
        import render
        render.render(args.out, args.pdf)


if __name__ == "__main__":
    main()
