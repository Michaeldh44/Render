# Playwright-basisimage heeft Chromium + alle systeem-libs al aan boord.
# Versie MOET gelijk zijn aan de playwright-pin in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
ENV PORT=8080
EXPOSE 8080
# shell-vorm zodat ${PORT} (o.a. Cloud Run zet die) wordt ingevuld
CMD ["sh","-c","uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
