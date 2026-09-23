# syntax=docker/dockerfile:1
#
# Kenyan Stock Analyzer — Docker image
#
# Multi-stage build:
#   1. "builder" — installs build tools + compiles the Python dependency set
#      into a venv. Nothing from this stage ships in the final image.
#   2. final     — a slim runtime image: the compiled venv + app code +
#      supercronic (the scheduler) + only the SHARED LIBRARIES WeasyPrint and
#      the OCR fallback actually need at runtime, running as a non-root user.
#
# Base image and every version pinned below (Python, Debian codename,
# supercronic + its checksums) were verified against the live upstream
# sources at the time this Dockerfile was written — see README.md's Docker
# section for how to re-verify them yourself before you rely on this in
# production.

ARG PYTHON_VERSION=3.14
ARG DEBIAN_CODENAME=bookworm

# ============================================================================
# Stage 1: builder
# ============================================================================
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_CODENAME} AS builder

# build-essential + the *-dev headers below are only needed if pip has to
# compile a package from source (no prebuilt wheel for this exact Python
# version/architecture yet). They add real minutes and ~200MB — but leaving
# them out risks a hard build failure the day a wheel is missing, which is
# worse. None of this reaches the final image (multi-stage).
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      python3-dev \
      libffi-dev \
      libcairo2-dev \
      pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ============================================================================
# Stage 2: final runtime image
# ============================================================================
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_CODENAME} AS final

# ---- Runtime system libraries ----
# WeasyPrint (PDF generation): libpango/libpangocairo/libgdk-pixbuf/libcairo/
#   libffi/libjpeg — this exact list is the one already proven in this
#   project's own GitHub Actions CI (.github/workflows/daily-summary.yml),
#   which builds and runs the same pipeline on an Ubuntu (Debian-family)
#   runner; Debian bookworm uses the same package names.
# tesseract-ocr + poppler-utils: the NSE-PDF OCR fallback data source
#   (src/data_acquisition.py) genuinely calls pytesseract + pdf2image, which
#   need the tesseract and poppler (pdftoppm) system binaries respectively —
#   without these the fallback silently can't run if the primary source ever
#   needs it.
# ca-certificates: TLS trust store for every HTTPS fetch this app makes
#   (TradingView, CBK, Google News, mystocks, open.er-api, yfinance). Unlike
#   macOS/Homebrew Python, a standard Debian Python + ca-certificates needs
#   no SSL_CERT_FILE override — that workaround in run.sh is macOS-only and
#   is deliberately NOT reproduced here.
# tzdata: so TZ=Africa/Nairobi (set below) is a real, valid timezone the
#   container can observe — both for supercronic's schedule and for every
#   naive datetime.now() call in the app itself.
# curl: used by the Docker HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 \
      libpangocairo-1.0-0 \
      libgdk-pixbuf-2.0-0 \
      libcairo2 \
      libffi8 \
      libjpeg62-turbo \
      tesseract-ocr \
      poppler-utils \
      ca-certificates \
      tzdata \
      curl \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Africa/Nairobi
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# ---- supercronic — a cron-compatible job runner built for containers ----
# Why supercronic instead of plain cron: it logs straight to stdout/stderr
# (so `docker compose logs` shows the schedule firing — plain cron needs a
# syslog daemon for that, which containers don't have by default), needs no
# root/setuid trickery to run under a non-root user's crontab, and handles
# signals correctly as the process a container supervises. It is the
# standard, widely-recommended choice for "run this on a schedule inside a
# container" (see README.md's Docker section for the full reasoning).
#
# Version + per-architecture SHA1 checksums below were pulled directly from
# https://github.com/aptible/supercronic/releases/tag/v0.2.49 at the time
# this Dockerfile was written — verify against that URL yourself if you're
# auditing this before a production deployment. TARGETARCH is supplied
# automatically by Docker BuildKit/buildx; this builds correctly for both
# linux/amd64 and linux/arm64 (Raspberry Pi 4/5, Apple Silicon, etc.).
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA1SUM_AMD64=e63c11a9726b775a6a11801e81af4f3fb926aa68
ARG SUPERCRONIC_SHA1SUM_ARM64=0b6c5bb743e0b0dafed1132198c81807927ac413
RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) SUPERCRONIC_SHA1SUM="${SUPERCRONIC_SHA1SUM_AMD64}" ;; \
      arm64) SUPERCRONIC_SHA1SUM="${SUPERCRONIC_SHA1SUM_ARM64}" ;; \
      *) echo "Unsupported TARGETARCH: ${TARGETARCH} (only amd64/arm64 are pinned — see README)"; exit 1 ;; \
    esac; \
    SUPERCRONIC_URL="https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}"; \
    curl -fsSL -o /usr/local/bin/supercronic "${SUPERCRONIC_URL}"; \
    echo "${SUPERCRONIC_SHA1SUM}  /usr/local/bin/supercronic" | sha1sum -c -; \
    chmod +x /usr/local/bin/supercronic

# ---- app ----
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --shell /bin/bash --create-home app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY --chown=app:app . .
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# data/ reports/ logs/ portfolio/ are all volume mount points (see
# docker-compose.yml) — created + owned by `app` here so the FIRST run
# doesn't fail if a mount point doesn't already exist. If you bind-mount a
# host directory over one of these, make sure that host directory is
# writable by UID 1000 (see the README's "permissions" note) or the app
# will get a PermissionError instead of a friendly log line.
RUN mkdir -p data reports logs portfolio && chown -R app:app /app

USER app

# ENABLE_OFFICIAL_CLOSE's own comment says "best run after 15:00 EAT" — the
# default schedule here is deliberately 16:00 EAT (an hour after close, once
# the official closing prices have settled), weekdays only. Override with
# SCHEDULE_CRON (standard 5-field crontab syntax, interpreted in TZ above).
ENV SCHEDULE_CRON="0 16 * * 1-5"

# supercronic itself isn't a request-serving process, so "healthy" here just
# means "the scheduler is alive" — good enough to let `docker compose ps` /
# an orchestrator flag a crashed container instead of silently never running
# again.
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f supercronic || exit 1

ENTRYPOINT ["/entrypoint.sh"]
