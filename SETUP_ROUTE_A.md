# Route A — losse test-tool op GitHub + Render

Doel: de dakscan-engine als **aparte test-tool** online, met een publieke
URL die je kunt delen — helemaal los van het portaal. Geen herbouw: dezelfde
Python-service die we al getest hebben.

Waarom Render (en niet Vercel): de PDF wordt door Chromium gemaakt, en dat
draait niet op Vercel. Render draait wél containers, koppelt net zo makkelijk
aan je GitHub-repo, en heeft een gratis tier. (Railway werkt identiek — zie
onderaan.)

Wat er live komt: één service met drie endpoints —
`/health` (leeft ie?), `/demo` (voorbeeld-PDF, geen PDOK nodig), en
`/specblad` (echt: adres → PDF).

---

## Wat je nodig hebt
- Een GitHub-account.
- Een Render-account (gratis, inloggen kan met GitHub): https://render.com
- De bestanden uit dit pakket (staan klaar).

---

## Stap 1 — Repo op GitHub

Maak een nieuwe repo (bijv. `dakscan`) en zet deze bestanden erin:

```
app.py              geo_sources.py     build_dakspec.py
render.py           luchtfoto.py       run.py
requirements.txt    Dockerfile         .dockerignore
render.yaml         README.md          DEPLOY.md
```

Via de command line:
```bash
cd dakscan
git init
git add .
git commit -m "dakscan test-service"
git branch -M main
git remote add origin https://github.com/<jouw-naam>/dakscan.git
git push -u origin main
```
(Of gewoon de bestanden slepen in "Add file → Upload files" op github.com.)

---

## Stap 2 — Deployen op Render

**Handmatig (meest betrouwbaar):**
1. Render → **New → Web Service**.
2. Koppel je GitHub en kies de `dakscan`-repo.
3. Render ziet de **Dockerfile** en zet "Runtime: Docker" vanzelf. Verder:
   - **Region:** Frankfurt (EU)
   - **Instance type:** Free
   - **Health Check Path:** `/health`
4. **Create Web Service.** De eerste build duurt een paar minuten (Chromium
   zit al in het basisimage, dus geen extra installatie).
5. Je krijgt een URL als `https://dakscan-xxxx.onrender.com`.

**Of via de blueprint:** New → **Blueprint** → kies de repo → Render leest
`render.yaml` en zet het bovenstaande automatisch. Werkt de validatie niet
mee, val dan terug op de handmatige stappen.

---

## Stap 3 — Testen

**a) Smoketest (geen PDOK nodig)** — open in je browser:
```
https://dakscan-xxxx.onrender.com/demo
```
Krijg je een A3-PDF met het voorbeeld-dak? Dan staat de deploy goed.

**b) Echt adres** — vanaf je eigen machine:
```bash
curl -s -X POST https://dakscan-xxxx.onrender.com/specblad \
     -H "content-type: application/json" \
     -d '{"adressen":["Zaandammerstraat 12, Zaandam"],"ref":"test","luchtfoto":true}' \
     -o test.pdf
```
Open `test.pdf`.

---

## Eerlijk — waar je op moet rekenen
- **Koude start:** de gratis tier slaapt na ~15 min inactiviteit. De eerste
  request daarna duurt 30–60 s. Prima om te testen; niet voor productie.
- **`/demo` werkt, `/specblad` niet?** Dan zit het in de open-data-laag, niet
  in de deploy: de PDOK-endpoint of laagnaam bovenaan `geo_sources.py` /
  `luchtfoto.py` moet mogelijk één regel bijgesteld. Dit is precies het
  stukje dat ik niet live kon testen.
- **Openbaar:** met de gratis tier staat de URL open op internet. Voor een
  echte test met adressen kun je beter een token-check toevoegen (zeg maar,
  dan zet ik die in `app.py`), of 'm privé zetten.

---

## Railway in plaats van Render (identiek idee)
1. https://railway.app → **New Project → Deploy from GitHub repo**.
2. Kies de repo; Railway pakt de Dockerfile.
3. **Settings → Networking → Generate Domain** voor een publieke URL.
Zelfde endpoints, zelfde tests.
