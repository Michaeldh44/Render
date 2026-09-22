#!/usr/bin/env python3
"""
dakscan specblad generator (proof of concept)
---------------------------------------------
Input : a "dakspec" JSON file (the contract the engine must produce).
Output: an A3-landscape PDF specblad, rendered via Chromium (same path
        as the reference sheet), for BOTH flat (plat) and pitched
        (hellend) roofs.

Usage:  python3 render.py samples/plat.json out/plat.pdf
"""
import json, sys, html, base64, os
from playwright.sync_api import sync_playwright

def _img_data_uri(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()

# ---------- geometry -> SVG ----------------------------------------------

SVG_W, SVG_H = 620, 360            # svg drawing viewbox (px)
MARGIN = 46

def _transform(spec):
    W = spec["omhullende"]["lengte_m"]      # x = length (horizontal)
    H = spec["omhullende"]["breedte_m"]     # y = width  (vertical)
    sx = (SVG_W - 2*MARGIN) / W
    sy = (SVG_H - 2*MARGIN) / H
    s = min(sx, sy)
    ox = (SVG_W - s*W) / 2
    oy = (SVG_H - s*H) / 2
    def T(p):
        return (ox + p[0]*s, oy + p[1]*s)
    return T, s, W, H, ox, oy

def _poly(pts, T):
    return " ".join(f"{T(p)[0]:.1f},{T(p)[1]:.1f}" for p in pts)

def _centroid(pts):
    cx = sum(p[0] for p in pts)/len(pts)
    cy = sum(p[1] for p in pts)/len(pts)
    return cx, cy

def build_svg(spec):
    T, s, W, H, ox, oy = _transform(spec)
    g = spec["geometrie"]
    plat = spec["daktype"] == "plat"
    e = []
    e.append(f'<svg viewBox="0 0 {SVG_W} {SVG_H}" xmlns="http://www.w3.org/2000/svg" font-family="Helvetica,Arial,sans-serif">')

    # --- gutter / opstand band for pitched ---
    if not plat and g.get("goot", {}).get("toon"):
        # outer envelope band
        env = [[0,0],[W,0],[W,H],[0,H]]
        e.append(f'<polygon points="{_poly(env,T)}" fill="#eef3f7" stroke="none"/>')

    # --- roof planes ---
    fill = "#f6e7e0" if not plat else "#eef1f4"
    for dv in g["dakvlakken"]:
        e.append(f'<polygon points="{_poly(dv["punten"],T)}" fill="{fill}" '
                 f'stroke="#333" stroke-width="1.1"/>')

    # opstand (double line) for flat roof
    if plat and g.get("opstand", {}).get("toon"):
        for dv in g["dakvlakken"]:
            p = _poly(dv["punten"], T)
            e.append(f'<polygon points="{p}" fill="none" stroke="#8aa0b4" '
                     f'stroke-width="3.2" opacity="0.5"/>')

    # hip rafters + ridge for pitched
    if not plat:
        for hk in g.get("hoekkepers", []):
            a, b = T(hk[0]), T(hk[1])
            e.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" '
                     f'y2="{b[1]:.1f}" stroke="#c0562f" stroke-width="1.3"/>')
        if g.get("nok"):
            a, b = T(g["nok"][0]), T(g["nok"][1])
            e.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" '
                     f'y2="{b[1]:.1f}" stroke="#111" stroke-width="2"/>')

    # --- components ---
    for od in g.get("onderdelen", []):
        if "poly" in od:                       # maatvaste contour
            pts = " ".join(f"{T(p)[0]:.1f},{T(p)[1]:.1f}" for p in od["poly"])
            e.append(f'<polygon points="{pts}" fill="#dfe6ee" stroke="#334" stroke-width="1"/>')
            cx = sum(T(p)[0] for p in od["poly"]) / len(od["poly"])
            cy = sum(T(p)[1] for p in od["poly"]) / len(od["poly"])
            e.append(f'<text x="{cx:.1f}" y="{cy+3:.1f}" font-size="9" font-weight="bold" '
                     f'text-anchor="middle" fill="#111">{od["nr"]}</text>')
            continue
        if "positie" not in od:
            continue
        px, py = T(od["positie"])
        gw, gh = od.get("grootte", [0.6, 0.6])
        w, h = gw*s, gh*s
        dash = 'stroke-dasharray="3,2"' if od.get("status") == "opgave" else ""
        e.append(f'<rect x="{px-w/2:.1f}" y="{py-h/2:.1f}" width="{w:.1f}" '
                 f'height="{h:.1f}" fill="#dfe6ee" stroke="#334" stroke-width="1" {dash}/>')
        e.append(f'<text x="{px:.1f}" y="{py+3:.1f}" font-size="9" font-weight="bold" '
                 f'text-anchor="middle" fill="#111">{od["nr"]}</text>')
        if od["type"] in ("dakraam", "installatie"):
            e.append(f'<text x="{px:.1f}" y="{py+h/2+11:.1f}" font-size="7.5" '
                     f'text-anchor="middle" fill="#333">{html.escape(od["label"])}</text>')

    # --- roof-plane letters in circles ---
    for dv in g["dakvlakken"]:
        cx, cy = _centroid(dv["punten"])
        px, py = T([cx, cy])
        e.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="12" fill="#fff" '
                 f'stroke="#111" stroke-width="1.3"/>')
        e.append(f'<text x="{px:.1f}" y="{py+4.5:.1f}" font-size="13" '
                 f'font-weight="bold" text-anchor="middle" fill="#111">{dv["label"]}</text>')

    # --- dimension lines (omhullende) ---
    top_y = oy - 16
    x0, x1 = ox, ox + W*s
    e.append(f'<line x1="{x0}" y1="{top_y}" x2="{x1}" y2="{top_y}" stroke="#2b6cb0" stroke-width="1"/>')
    for xx in (x0, x1):
        e.append(f'<line x1="{xx}" y1="{top_y-4}" x2="{xx}" y2="{top_y+4}" stroke="#2b6cb0" stroke-width="1"/>')
    e.append(f'<text x="{(x0+x1)/2:.1f}" y="{top_y-5:.1f}" font-size="10" font-weight="bold" '
             f'text-anchor="middle" fill="#2b6cb0">{W:.2f} m omhullend</text>')

    left_x = ox - 16
    y0, y1 = oy, oy + H*s
    e.append(f'<line x1="{left_x}" y1="{y0}" x2="{left_x}" y2="{y1}" stroke="#2b6cb0" stroke-width="1"/>')
    for yy in (y0, y1):
        e.append(f'<line x1="{left_x-4}" y1="{yy}" x2="{left_x+4}" y2="{yy}" stroke="#2b6cb0" stroke-width="1"/>')
    e.append(f'<text x="{left_x-6:.1f}" y="{(y0+y1)/2:.1f}" font-size="10" font-weight="bold" '
             f'text-anchor="middle" fill="#2b6cb0" transform="rotate(-90 {left_x-6:.1f} {(y0+y1)/2:.1f})">'
             f'{H:.2f} m omhullend</text>')

    # --- north arrow ---
    nx, ny = SVG_W - 24, 26
    e.append(f'<line x1="{nx}" y1="{ny+12}" x2="{nx}" y2="{ny-12}" stroke="#111" stroke-width="1.4"/>')
    e.append(f'<polygon points="{nx-4},{ny-6} {nx+4},{ny-6} {nx},{ny-13}" fill="#111"/>')
    e.append(f'<text x="{nx}" y="{ny+22}" font-size="8" text-anchor="middle" fill="#111">N</text>')

    e.append('</svg>')
    return "\n".join(e)

# ---------- HTML template -------------------------------------------------

def rows_html(rows):
    out = []
    for r in rows:
        val = html.escape(str(r["waarde"]))
        eenheid = r.get("eenheid", "")
        mk = r.get("maatklasse")
        badge = f'<span class="mk mk{mk}">{mk}</span>' if mk else ""
        out.append(f'<tr><td class="lbl">{html.escape(r["label"])}</td>'
                    f'<td class="val">{val}&nbsp;{eenheid}{badge}</td></tr>')
    return "\n".join(out)

def opp_html(o):
    out = []
    for r in o["per_dakvlak"]:
        out.append(f'<tr><td class="lbl">{html.escape(r["label"])}</td>'
                   f'<td class="val">{r["m2"]:.2f} m&sup2;</td></tr>')
    if o.get("netto_aftrek_m2"):
        out.append(f'<tr><td class="lbl">Aftrek obstakels (netto)</td>'
                   f'<td class="val">- {o["netto_aftrek_m2"]:.2f} m&sup2;</td></tr>')
    out.append(f'<tr class="tot"><td class="lbl">TOTAAL</td>'
               f'<td class="val">{o["totaal_m2"]:.2f} m&sup2;</td></tr>')
    return "\n".join(out)

def obj_html(objecten):
    zk = {"hoog": "A", "midden": "B", "laag": "C", "A": "A", "B": "B", "C": "C"}
    out = []
    for o in objecten:
        mk = zk.get(o.get("zekerheid", "laag"), "C")
        naam = o.get("type", "overig").replace("_", " ")
        m2 = f' &middot; {o["m2"]:.1f} m&sup2;' if o.get("m2") else ""
        out.append(f'<tr><td class="lbl">[{o["nr"]}] {html.escape(naam)}'
                   f'{" &middot; " + html.escape(o["omschrijving"]) if o.get("omschrijving") else ""}'
                   f'{m2}</td>'
                   f'<td class="val"><span class="mk mk{mk}">{mk}</span></td></tr>')
    return "\n".join(out) or '<tr><td class="lbl">geen objecten gedetecteerd</td><td></td></tr>'


STYLE = """
@page { size: A3 landscape; margin: 0; }
* { box-sizing: border-box; }
body { margin:0; font-family: Helvetica, Arial, sans-serif; color:#111; }
.sheet { width:420mm; height:297mm; padding:9mm 11mm; position:relative; break-after:page; }
.sheet:last-child { break-after:auto; }
.header { display:flex; align-items:center; gap:10mm; border-bottom:2px solid #111; padding-bottom:3mm; }
.brand { display:flex; align-items:center; gap:3mm; }
.logo { background:#111; color:#fff; font-weight:800; letter-spacing:.5px; padding:3mm 4mm; font-size:15pt; border-radius:2px; }
.logo small { font-weight:500; opacity:.7; }
.title { font-size:12.5pt; font-weight:700; line-height:1.25; }
.title .sub { font-weight:500; color:#333; font-size:10.5pt; }
.body { display:grid; grid-template-columns: 55% 45%; gap:8mm; margin-top:5mm; }
.draw { border:1px solid #cfd6dd; border-radius:3px; padding:4mm; }
.draw svg { width:100%; height:auto; }
.cap { font-size:7.5pt; color:#333; margin-top:2mm; line-height:1.35; }
.visual { margin-top:5mm; border:1px solid #cfd6dd; border-radius:3px; height:62mm;
          background:repeating-linear-gradient(45deg,#eef1f4,#eef1f4 8px,#e6eaef 8px,#e6eaef 16px);
          display:flex; align-items:center; justify-content:center; color:#8792a0; font-size:9pt; text-align:center; overflow:hidden; }
.visual img { width:100%; height:100%; object-fit:cover; display:block; }
.mainviz { border:1px solid #cfd6dd; border-radius:3px; overflow:hidden; background:#eef1f4;
           height:172mm; display:flex; align-items:center; justify-content:center; }
.mainviz img { width:100%; height:100%; object-fit:contain; display:block; }
.vcap { font-size:7.5pt; margin-top:1.5mm; line-height:1.35; }
.vcap b { color:#111; } .vcap .warn { color:#b45309; }
h2 { font-size:10.5pt; margin:0 0 1.5mm; border-bottom:1px solid #111; padding-bottom:1mm; }
.block { margin-bottom:6mm; }
table { width:100%; border-collapse:collapse; }
td { padding:1.1mm 0; font-size:9pt; vertical-align:top; }
td.lbl { color:#333; }
td.val { text-align:right; font-weight:700; white-space:nowrap; }
tr { border-bottom:1px dotted #dfe3e8; }
tr.tot td { border-top:1.5px solid #111; font-size:10pt; padding-top:1.5mm; }
.mk { display:inline-block; margin-left:2mm; font-size:6.5pt; font-weight:700; color:#fff; border-radius:2px; padding:0 1mm; vertical-align:middle; }
.mkA { background:#2f855a; } .mkB { background:#b7791f; } .mkC { background:#c05621; }
.pill { display:inline-block; font-size:8pt; font-weight:700; background:#111; color:#fff; padding:1mm 2.5mm; border-radius:3px; margin-left:4mm; }
.footer { position:absolute; bottom:6mm; left:11mm; right:11mm; border-top:1px solid #cfd6dd;
          padding-top:1.5mm; font-size:6.5pt; color:#667; display:flex; justify-content:space-between; gap:6mm; }
.footer .ref { white-space:nowrap; font-weight:700; color:#334; }
"""


def build_sheet(spec):
    m = spec["meta"]
    svg = build_svg(spec)
    verdict = m.get("verdict", "")
    material = spec["materiaal"]
    pill = f'<span class="pill">{html.escape(spec["pagina_label"])}</span>' if spec.get("pagina_label") else ""
    legenda = "cirkel = dakvlak (letter), vakje = onderdeel [nr] · noord ~ boven (RD)"
    legenda2 = "buitenmaten = omhullende · vakjes = objecten (Vision)"

    dv = spec["dakvisual"]
    heeft_foto = (dv.get("type") == "image" and dv.get("bestand") and os.path.exists(dv["bestand"]))
    if heeft_foto:
        img = f'<img src="{_img_data_uri(dv["bestand"])}" alt="luchtfoto"/>'
        cap = f"DAKVISUAL — {html.escape(dv.get('onderschrift',''))} · noord ~ boven (RD) · schaalbalk 5 m"
        left_html = (f'<div class="mainviz">{img}</div><div class="cap">{cap}</div>'
                     f'<div class="vcap"><span class="warn">{html.escape(dv.get("let_op",""))}</span></div>')
    else:
        left_html = (f'<div class="draw">{svg}<div class="cap">schaal {html.escape(spec["geometrie"]["schaal"])} · {legenda}<br>{legenda2}</div></div>'
                     f'<div class="visual">DAKVISUAL<br>(luchtfoto — placeholder)</div>')

    right = (f'<div class="block"><h2>DAKGEGEVENS</h2><table>{rows_html(spec["dakgegevens"])}</table></div>'
             f'<div class="block"><h2>DAKOPPERVLAKTE PER DAKVLAK</h2><table>{opp_html(spec["oppervlakte"])}</table></div>')
    if spec.get("objecten_lijst") is not None:
        right += (f'<div class="block"><h2>OBJECTEN OP DAK (Vision)</h2>'
                  f'<table>{obj_html(spec["objecten_lijst"])}</table></div>')
    right += (f'<div class="block"><h2>{html.escape(material["kop"])}</h2>'
              f'<table>{rows_html([{"label":r["label"],"waarde":r["waarde"],"eenheid":""} for r in material["rijen"]])}</table></div>')

    return f"""<div class="sheet">
  <div class="header">
    <div class="brand"><div class="logo">dakscan<small> · specblad</small></div></div>
    <div class="title">{html.escape(m["titel"])}{pill}<br>
      <span class="sub">{html.escape(m.get("regel",""))} &nbsp;·&nbsp; {html.escape(m.get("datum",""))}</span></div>
  </div>
  <div class="body">
    <div class="left">{left_html}</div>
    <div class="right">{right}</div>
  </div>
  <div class="footer"><div>{html.escape(spec['bronnen'])}</div>
    <div class="ref">{html.escape(m['ref'])} · {verdict}</div></div>
</div>"""


def build_doc(specs):
    sheets = "\n".join(build_sheet(s) for s in specs)
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<style>{STYLE}</style></head><body>{sheets}</body></html>')


def build_html(spec):
    return build_doc([spec])

# ---------- render --------------------------------------------------------

def _to_pdf(doc, out_path):
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.set_content(doc, wait_until="networkidle")
        page.pdf(path=out_path, prefer_css_page_size=True, print_background=True)
        b.close()
    print("wrote", out_path)


def render_specs(specs, out_path):
    """specs: lijst van pagina-specs -> één PDF met meerdere pagina's."""
    _to_pdf(build_doc(specs), out_path)


def render_specs_bytes(specs):
    """Zelfde als render_specs, maar geeft de PDF-bytes terug (voor de service)."""
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.set_content(build_doc(specs), wait_until="networkidle")
        pdf = page.pdf(prefer_css_page_size=True, print_background=True)
        b.close()
    return pdf


def render(spec_path, out_path):
    with open(spec_path, encoding="utf-8") as f:
        data = json.load(f)
    specs = data if isinstance(data, list) else [data]
    _to_pdf(build_doc(specs), out_path)

if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2])

VERSION = "r6-2026-09-22"
