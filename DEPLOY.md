# dakscan — deployen

De engine is nu een HTTP-service (`app.py`) in een container. Dit is wat
je nodig hebt om 'm live te zetten en aan je portaal te hangen.

## Wat draait waar

- De service **heeft internet nodig tijdens runtime** (PDOK Locatieserver,
  BAG WFS, 3D BAG, luchtfoto). Elke cloud-host of je eigen server met
  internet voldoet. Niet geschikt: Vercel/Supabase Edge Functions —
  Chromium (voor de PDF) past niet in serverless. Daarom: een **container**.
- De PDF-render gebruikt Chromium; dat zit al in het Playwright-basisimage,
  dus je hoeft daar niets voor te installeren.

## Endpoints

| Methode | Pad | Doel | Netwerk |
|---|---|---|---|
| GET | `/health` | leeft de service? | nee |
| GET | `/demo` | voorbeeld-PDF (ingebouwd blok) | **nee** → smoketest |
| POST | `/specblad` | echt: adres(sen) → PDF/JSON | ja (PDOK) |

`/specblad` body:
```json
{ "adressen": ["Zaandammerstraat 12, Zaandam"], "ref": "zd-000481",
  "luchtfoto": true, "formaat": "pdf" }
```

## Lokaal testen (Docker)

```bash
docker build -t dakscan .
docker run -p 8080:8080 dakscan

# 1) smoketest — geen PDOK nodig, bewijst dat de container rendert:
curl -s localhost:8080/demo -o demo.pdf && open demo.pdf

# 2) echte run — heeft PDOK nodig:
curl -s -X POST localhost:8080/specblad \
     -H "content-type: application/json" \
     -d '{"adressen":["Zaandammerstraat 12, Zaandam"],"ref":"test","luchtfoto":true}' \
     -o test.pdf && open test.pdf
```

Werkt `/demo` maar `/specblad` niet, dan zit het in de open-data-laag
(endpoint/laagnaam bovenaan `geo_sources.py` / `luchtfoto.py`), niet in de
deploy.

## Hosten — twee routes

**A. Google Cloud Run (aanrader: minste beheer, schaalt naar nul, goedkoop)**
```bash
gcloud run deploy dakscan --source . --region europe-west4 \
       --no-allow-unauthenticated --memory 1Gi --timeout 120
```
- `--no-allow-unauthenticated`: alleen bereikbaar met een Google-token →
  je portaal roept 'm intern aan, niet het open internet.
- 1 GiB geheugen is ruim voor Chromium bij lage volumes.

**B. Eigen server / VPS (alles in eigen hand)**
```bash
docker build -t dakscan .
docker run -d --restart unless-stopped -p 8080:8080 --name dakscan dakscan
```
Zet er een reverse proxy (nginx/Caddy) vóór met basic-auth of een
token-header, en houd 'm op je interne netwerk.

## Aan het portaal hangen

Je portaal (Next.js) doet server-side een `POST /specblad` en streamt de
PDF terug naar de gebruiker. Houd de service **privé** (intern netwerk of
token). Voeg desgewenst een simpele bearer-token-check toe in `app.py` —
zeg maar als je die erbij wilt.

## Nog te doen vóór "productie-af"
- Live-verificatie van de PDOK-endpoints (kon ik niet testen).
- Token/auth op de service als hij buiten je interne netwerk bereikbaar is.
- Afschot uit AHN + DXF-export (staan in de README als vervolgstappen).
