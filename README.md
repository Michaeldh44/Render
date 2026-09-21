# dakscan — platte-daken engine (v0.1 PoC)

Adres(sen) → open data → **specblad** (A3) voor platte daken.
Voorcalculatie-tool: snel een dak uit een adres, met een ingebouwde
betrouwbaarheidslaag. **Inmeting op locatie blijft leidend voor het bestek.**

## Twee helften

| Bestand | Wat | Netwerk? | Status |
|---|---|---|---|
| `geo_sources.py` | adres → BAG-footprint + 3D BAG-hoogte (PDOK / TU Delft) | **ja (PDOK)** | in jouw omgeving verifiëren |
| `build_dakspec.py` | geometrie → dakspec-JSON (oppervlak, omtrek, maatklassen) | nee | **getest** ✓ |
| `render.py` | dakspec-JSON → A3-PDF (Chromium) | nee | **getest** ✓ |
| `run.py` | CLI die alles aan elkaar knoopt | ja | draai bij jou |

Het **datacontract** (de JSON) is de scheiding: `build`/`render` staan vast,
alleen `geo_sources` raakt het live netwerk.

## Draaien (in jouw omgeving, met PDOK-toegang)

```bash
pip install shapely pyproj requests playwright
playwright install chromium

python3 run.py "Zaandammerstraat 12, Zaandam" "Zaandammerstraat 14, Zaandam" \
        --ref zd-000481-blok2 --out out/blok2.json --pdf out/blok2.pdf
```

Meerdere adressen → panden worden samengevoegd tot één dak.

## Wat ik NIET live kon testen
De PDOK/3D BAG-calls in `geo_sources.py` (mijn sandbox mag daar niet bij).
Endpoints en veldnamen staan als constanten bovenaan het bestand; als PDOK
iets hernoemt pas je alleen die aan. Alle sleutel-loze paden gekozen: **geen
API-key nodig.**

## Eerste verfijningen (in volgorde)
1. **Krappere omhullende** — nu axis-aligned bbox (noord boven); voor gedraaide
   gebouwen `minimum_rotated_rectangle` als optie (tekening draait dan mee).
2. **Afschot uit AHN** — DSM-raster over de footprint samplen → vlak fitten → %.
   Zet maatklasse afschot van C naar B.
3. **Obstakels** — koepels/HWA half automatisch uit AHN-hoogteafwijkingen,
   rest als opgave.
4. **DXF-export** — de footprint + maatvoering als echte DXF (RD-gerefereerd).
5. **Meetrapport** — per maat de bron + maatklasse als los blad.
