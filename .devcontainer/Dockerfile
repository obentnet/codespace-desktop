FROM mcr.microsoft.com/devcontainers/base:ubuntu24.04

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
