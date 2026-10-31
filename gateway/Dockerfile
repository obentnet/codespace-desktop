FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends iptables ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY config.py relay.py entrypoint.py healthcheck.py ./
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 XTABLES_LOCKFILE=/tmp/xtables.lock
EXPOSE 3000
ENTRYPOINT ["python3", "/app/entrypoint.py"]
HEALTHCHECK --interval=30s --timeout=15s --start-period=20s --retries=2 \
    CMD ["python3", "/app/healthcheck.py"]
