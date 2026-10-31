#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  printf '%s\n' 'Docker 不可用，无法确认或停止容器。' >&2
  exit 1
fi

docker compose down --remove-orphans
printf '%s\n' 'Chrome 与代理网关已停止，browser-data/ 和 proxy.json 已保留。'
