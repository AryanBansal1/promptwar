# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENV=production \
    PORT=8080

WORKDIR /srv

COPY requirements.txt .
RUN pip install --upgrade pip setuptools \
    && pip install -r requirements.txt

COPY app ./app
COPY static ./static

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 saathi && chown -R saathi:saathi /srv
USER saathi

EXPOSE 8080

# Cloud Run sets $PORT. Proxy headers give the real client IP for per-IP rate limiting.
# Access logs are disabled: our middleware logs request id, route, status and latency only.
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --no-access-log --no-server-header"]
