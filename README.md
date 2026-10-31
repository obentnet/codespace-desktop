# Codespaces 云端 Chrome

在 GitHub Codespaces 运行 Google Chrome，通过私有网页操作。**填写 SOCKS5 配置并验证通过后，Chrome 才会启动。代理断开时禁止回退直连。**

```text
本地浏览器 ── GitHub 私有 HTTPS ── 3000 控制入口
                                      │
                                    Chrome
                                      │
                             本地 SOCKS5 转接服务
                                      │
                              唯一的 SOCKS5 节点
                                      │
                                   目标网站
```

## 配置与启动

1. [创建 Codespace](https://codespaces.new/obentnet/codespace-desktop)，或从本仓库的 **Code → Codespaces** 创建，先使用 2 核机器即可。
2. 首次打开会生成仓库根目录的 `proxy.json`，Chrome 保持停止。编辑此文件：

   ```json
   {
     "server": "你的代理节点 IPv4",
     "port": 1080,
     "username": "你的代理用户名",
     "password": "你的代理密码",
     "connect_timeout_seconds": 15
   }
   ```

   `server` 必须是固定 IPv4，不带 `socks5://`，不能填域名。`port` 填整数。无认证节点的 `username`、`password` 同时填空字符串。密码含双引号或反斜杠时按 JSON 规则转义。
3. 在终端执行：

   ```bash
   bash scripts/start.sh
   ```

4. 等待看到“Chrome 已通过代理启动”。脚本会检查配置、停止旧容器、构建网关，并经代理验证远端 DNS、TLS 和 HTTPS，成功后启动 Chrome。
5. 在底部 **PORTS / 端口** 面板打开 **3000 / Cloud Chrome**。保持 **Private** 和 **HTTP**；浏览器最终访问的是 GitHub 提供的 **HTTPS** 地址。

未配置、配置错误、认证失败或节点不可用时均不会启动 Chrome。刚创建环境时不要提前打开 3000，否则没有服务会显示 502。节点需要允许经 SOCKS5 访问 `example.com:443`，这是启动及健康检查的目标。

## 已有 Codespace 升级

在现有 Codespace 的终端执行：

```bash
cd /workspaces/codespace-desktop
git pull --ff-only
bash scripts/start.sh
```

这会停止原来的 Chrome，并创建待填写的 `proxy.json`。填好后再次运行 `bash scripts/start.sh`。如果提示缺少 Python 或 Docker，请从命令面板执行 **Codespaces: Rebuild Container**。

## 日常使用

```bash
# 校验配置并重新启动；更换代理后也执行此命令
bash scripts/start.sh

# 查看容器、代理健康检查和控制入口状态
bash scripts/status.sh

# 停止 Chrome 和代理网关，保留浏览器数据
bash scripts/stop.sh

# 查看诊断日志，不显示节点密码
docker compose logs --tail=80 gateway chrome

# 更新 Chrome 后重新启动
docker compose pull chrome
bash scripts/start.sh
```

停止后，从 [Codespaces 列表](https://github.com/codespaces)重新打开原实例，配置有效且节点连通时会自动启动。代理中断时网页控制入口仍可用，Chrome 无法联网；节点恢复后可重新连接，必要时运行启动命令。

## 网络边界

- Chrome 与网关共享隔离的网络空间。防火墙先设为默认拒绝，只有网关转接进程能新建到**指定节点 IPv4 和 TCP 端口**的外部连接。
- Chrome 使用只读的受管代理策略；目标域名交给上游 SOCKS5 解析。直接 TCP、Docker DNS `127.0.0.11`、外部 DNS 和 IPv6 均被阻断。
- 仅支持 SOCKS5 **CONNECT / TCP**。UDP 和 QUIC 被阻断，WebRTC 禁用非代理 UDP；依赖这些功能的网页可能受影响。
- 网关只放行本地内部通信及 3000 控制连接的回复，不开放其他入站端口。控制入口继续通过 GitHub 私有 HTTPS 访问。
- 本限制针对 **Chrome 容器的网络**。Codespaces 管理、Git、构建及拉取镜像仍由 Codespace 正常联网。拥有 Codespace / Docker 管理权限的人仍能修改配置。

## 配置与数据

`proxy.json` 权限设为 `0600`，已在 `.gitignore` 排除，不会通过环境变量或命令行传递密码。凭据仅以只读文件挂载到网关，Chrome 容器不挂载此文件。示例文件 `proxy.example.json` 不含真实节点。

`browser-data/` 保存浏览器配置、登录状态和下载，也已被 Git 忽略。配置及浏览器数据都位于持久化工作区，停止、启动和重建开发容器时保留；删除 Codespace 前需单独备份。Git 忽略规则不提供备份。

Codespaces 默认闲置 30 分钟停止，个人设置可调整至 5–240 分钟；单次运行最长 12 小时。只操作 Chrome 控制网页不保证重置闲置计时。运行和保留存储会消耗 Codespaces 额度。

## 验证

```bash
# 标准库测试：配置校验、认证、域名转发、断线与启动流程
python3 -m unittest discover -s tests -p 'test_*.py' -v

# 真实 Docker 集成测试；用临时模拟节点，不使用你的代理配置
python3 tests/integration_proxy.py

# 同时验证真实 Chrome 的受管策略与控制入口
PROXY_TEST_REAL_CHROME=1 python3 tests/integration_proxy.py
```

在 Chrome 打开 `chrome://policy` 可检查代理、QUIC、DNS 和 WebRTC 策略。填写真实节点后，可访问你信任的 IP 查询网站确认浏览器出口；自动化测试验证的是网络约束，不证明任意供应商节点的质量。

## 资料

- [Chrome 镜像](https://docs.linuxserver.io/images/docker-chrome/)
- [Chrome SOCKS5 的域名解析及认证限制](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md)
- [Linux Chrome 受管策略](https://support.google.com/chrome/a/answer/9025927)
- [Docker 网络与 DNS](https://docs.docker.com/engine/network/)
- [Codespaces 端口转发](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace)
- [Codespaces 闲置与运行时间](https://docs.github.com/en/codespaces/setting-your-user-preferences/setting-your-timeout-period-for-github-codespaces)
