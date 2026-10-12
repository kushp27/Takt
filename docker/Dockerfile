# Takt image: one image runs both the web and the scheduler containers.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# supercronic runs cron jobs (ingest, nightly re-read, trust grade) in the scheduler container (eng review D1).
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.33
RUN set -eux; \
    apt-get update; apt-get install -y --no-install-recommends curl ca-certificates; rm -rf /var/lib/apt/lists/*; \
    case "$TARGETARCH" in \
      amd64) sha=71b0d58cc53f6bd72cf2f293e09e294b79c666d8 ;; \
      arm64) sha=e0f0c06ebc5627e43b25475711e694450489ab00 ;; \
      *) echo "unsupported arch $TARGETARCH"; exit 1 ;; \
    esac; \
    curl -fsSLo /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}"; \
    echo "${sha}  /usr/local/bin/supercronic" | sha1sum -c -; \
    chmod +x /usr/local/bin/supercronic

COPY requirements.txt .
RUN pip install -r requirements.txt

# Test target: adds pytest; used by docker-compose.test.yml and CI.
FROM base AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY . .

# Production target (default).
FROM base AS prod
COPY . .
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput
RUN useradd --system --uid 1000 takt && chown -R takt /app
USER takt
EXPOSE 8000
CMD ["docker/entrypoint-web.sh"]
