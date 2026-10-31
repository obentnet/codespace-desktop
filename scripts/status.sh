#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  printf '%s\n' 'Docker 不可用，无法读取容器状态。' >&2
  exit 1
fi

if [[ -f proxy.json ]]; then
  printf '%s\n' '本地代理配置：已存在（内容不显示）。'
else
  printf '%s\n' '本地代理配置：未创建。运行 bash scripts/start.sh 创建模板。'
fi

docker compose ps -a

if command -v curl >/dev/null 2>&1 && curl --noproxy '*' --fail --silent --max-time 3 http://127.0.0.1:3000/ >/dev/null; then
  printf '%s\n' 'Chrome 控制入口：可访问。代理状态请查看 gateway 的健康状态。'
else
  printf '%s\n' 'Chrome 控制入口：尚未就绪或已停止。'
fi
