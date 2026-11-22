# ---- ETF Me in the ETN -------------------------------------------------------
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/etfme.db PORT=8000
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY config ./config

ARG VERSION=dev
LABEL org.opencontainers.image.title="etf-me-in-the-etn" \
      org.opencontainers.image.description="Crash tracker for 2x/3x bull leveraged ETFs & ETNs" \
      org.opencontainers.image.source="https://github.com/dannyspace21/ETF-me-in-the-ETN" \
      org.opencontainers.image.version="${VERSION}"

RUN useradd -r -u 10001 etfme && mkdir -p /data && chown etfme /data
USER etfme
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz',timeout=4)"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
