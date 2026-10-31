#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

config_created=false
if [[ ! -e proxy.json && ! -L proxy.json ]]; then
  (umask 077; cp proxy.example.json proxy.json)
  config_created=true
fi

configuration_hint() {
  printf '%s\n' '请编辑仓库根目录的 proxy.json，填写 SOCKS5 节点的固定 IPv4、端口和认证信息。'
  printf '%s\n' '填写后运行：bash scripts/start.sh。代理配置不会提交到 Git。'
}

if ! command -v docker >/dev/null 2>&1; then
  if [[ "$config_created" == true ]]; then
    printf '%s\n' '已创建 proxy.json 配置模板，尚未启动 Chrome。'
    configuration_hint
    printf '%s\n' '当前没有 Docker，无法确认旧容器状态；请重建开发容器后启动。' >&2
    exit 0
  fi
  printf '%s\n' 'Docker 尚未安装。请在命令面板执行 Codespaces: Rebuild Container。' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  if [[ "$config_created" == true ]]; then
    printf '%s\n' '已创建 proxy.json 配置模板，尚未启动 Chrome。'
    configuration_hint
    printf '%s\n' 'Docker 服务不可用，无法确认旧容器状态；请重建开发容器后启动。' >&2
    exit 0
  fi
  printf '%s\n' 'Docker 服务尚未就绪。请重建容器，或稍后再次运行 bash scripts/start.sh。' >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  printf '%s\n' '缺少 Docker Compose。请在命令面板执行 Codespaces: Rebuild Container。' >&2
  exit 1
fi

# Stop the previous release as well: an old Chrome container may have had direct access.
# This removes containers and networks, while preserving browser-data/ on the workspace.
printf '%s\n' '正在停止现有 Chrome 环境，保留 browser-data/。'
docker compose down --remove-orphans

if [[ "$config_created" == true ]]; then
  printf '%s\n' '已创建 proxy.json 配置模板；Chrome 保持停止，等待填写代理。'
  configuration_hint
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' '缺少 Python 3，Chrome 已停止；请重建开发容器。' >&2
  exit 1
fi
if [[ ! -f proxy.json || -L proxy.json ]]; then
  printf '%s\n' 'proxy.json 必须是本地普通文件，不能是目录或符号链接；Chrome 已停止。' >&2
  exit 1
fi
chmod 600 proxy.json
if ! python3 gateway/config.py --check proxy.json; then
  printf '%s\n' '代理配置无效，Chrome 保持停止。修正 proxy.json 后重新运行启动命令。' >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  printf '%s\n' '缺少 curl，Chrome 已停止；请重建开发容器。' >&2
  exit 1
fi

mkdir -p browser-data
chmod 700 browser-data
export CHROME_PUID="$(id -u)"
export CHROME_PGID="$(id -g)"

starting_stack=false
cleanup_on_failure() {
  result=$?
  if [[ "$result" != 0 && "$starting_stack" == true ]]; then
    printf '%s\n' '启动未完成，正在停止环境，禁止回退到直连。' >&2
    docker compose down --remove-orphans >&2 || true
  fi
}
trap cleanup_on_failure EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf '%s\n' '正在构建代理网关并启动 Chrome，首次拉取镜像可能需要几分钟。'
printf '%s\n' '只有代理验证通过后才会启动 Chrome。'
starting_stack=true
docker compose up -d --build --wait --wait-timeout 180

for ((attempt = 1; attempt <= 60; attempt++)); do
  if curl --noproxy '*' --fail --silent --max-time 3 http://127.0.0.1:3000/ >/dev/null; then
    starting_stack=false
    if [[ -n "${CODESPACE_NAME:-}" ]]; then
      browser_url="https://${CODESPACE_NAME}-3000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}/"
    else
      browser_url='http://localhost:3000'
    fi
    printf '\n%s\n' "Chrome 已通过代理启动：${browser_url}"
    printf '%s\n' '在 Codespaces 的 PORTS / 端口面板打开 3000，保持 Private，端口协议保持 HTTP。'
    printf '%s\n' '代理断开时 Chrome 无法联网；控制入口仍可访问，不会回退到直连。'
    exit 0
  fi
  sleep 2
done

printf '%s\n' 'Chrome 网页入口未通过就绪检查，环境将停止。' >&2
printf '%s\n' '请运行 bash scripts/status.sh 查看状态，或 docker compose logs --tail=80 gateway chrome 检查日志。' >&2
exit 1
