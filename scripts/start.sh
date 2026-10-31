#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' 'Docker 尚未安装。请在命令面板执行 Codespaces: Rebuild Container。' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  printf '%s\n' 'Docker 服务尚未就绪。请重建容器，或稍后再次运行 bash scripts/start.sh。' >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  printf '%s\n' '缺少 Docker Compose。请在命令面板执行 Codespaces: Rebuild Container。' >&2
  exit 1
fi

mkdir -p browser-data
chmod 700 browser-data
export CHROME_PUID="$(id -u)"
export CHROME_PGID="$(id -g)"

docker compose up -d chrome

for ((attempt = 1; attempt <= 60; attempt++)); do
  if curl --fail --silent --max-time 3 http://127.0.0.1:3000/ >/dev/null; then
    printf '\n%s\n' 'Chrome 已启动：http://localhost:3000'
    printf '%s\n' '在 Codespaces 的 PORTS / 端口面板打开 3000，保持 Private，端口协议保持 HTTP。'
    printf '%s\n' '本地浏览器访问的是 GitHub 提供的 HTTPS 地址。'
    exit 0
  fi
  sleep 2
done

printf '%s\n' 'Chrome 尚未通过就绪检查。最近的启动日志：' >&2
docker compose logs --tail=80 chrome >&2
printf '%s\n' '首次初始化可能较慢，稍后运行 bash scripts/start.sh 重试。' >&2
exit 1
