# Codespaces 云端 Chrome

在 GitHub Codespaces 运行 Google Chrome，在本地浏览器里打开网页即可操作。网页访问、登录状态、下载和浏览器进程都在 Codespace 中。

采用 LinuxServer 的 Chrome 镜像，Selkies 提供画面、键盘、鼠标和剪贴板交互：

```text
本地浏览器 → GitHub 私有 HTTPS 转发 → Codespace 3000 → Chrome
```

## 启动

[创建 Codespace](https://codespaces.new/obentnet/codespace-desktop)

1. 点击上面的链接，或在本仓库选择 **Code → Codespaces → Create codespace on main**。可以先使用 2 核机器。
2. 等待开发环境构建和 Chrome 自动启动。首次启动需要下载镜像。
3. 打开底部 **PORTS / 端口** 面板，找到 **3000 / Cloud Chrome**，点击 **Open in Browser / 在浏览器中打开**。
4. 确认端口的可见性为 **Private**，端口协议为 **HTTP**。最终浏览器地址仍是 GitHub 提供的 **HTTPS** 地址。

端口会自动转发。若没有显示，在端口面板手动添加 `3000`。访问时使用创建 Codespace 的 GitHub 账号登录，应打开端口面板提供的地址。

仓库公开不代表 Chrome 端口公开；请保持浏览器端口为 **Private**。如果在加入本配置前已经创建过 Codespace，按 `Ctrl+Shift+P`（Mac 为 `Cmd+Shift+P`）执行 **Codespaces: Rebuild Container**。

## 日常使用

停止后，从 [Codespaces 列表](https://github.com/codespaces)重新打开原来的 Codespace，Chrome 会自动启动。无需每次新建 Codespace。

以下命令均在仓库根目录执行：

```bash
# 手动启动，重复运行不会删除浏览器数据
bash scripts/start.sh

# 查看状态或日志
docker compose ps
docker compose logs --tail=80 chrome

# 停止 Chrome；停止计算资源请另行停止 Codespace
docker compose stop chrome

# 更新 Chrome 镜像并启动
docker compose pull chrome
bash scripts/start.sh
```

## 数据与运行时间

`browser-data/` 对应容器的 `/config`，保存浏览器配置、登录状态及下载文件。它位于 Codespaces 持久化工作区，并已在 `.gitignore` 排除。停止再启动或重建开发容器时会保留；删除 Codespace 会删除这里的数据，需要的文件请先下载。Git 忽略规则并不提供备份。

端口保持 **Private**，由 GitHub 验证身份。镜像默认没有独立登录密码；不要将浏览器端口设为 Public，否则他人可能操作你的浏览器和容器。

Codespaces 当前默认闲置 30 分钟停止，个人设置可调整至 5–240 分钟；单次运行最长 12 小时。只操作转发的 Chrome 网页，不保证重置闲置计时。停止后需重新打开 Codespace。运行会消耗 Codespaces 计算额度，停止后保留的数据仍占用存储。

## 排查

- **页面连接失败**：确认 Codespace 正在运行，执行 `bash scripts/start.sh`，检查端口 3000 已转发。
- **Docker 不可用**：确认 `.devcontainer/devcontainer.json` 位于仓库根目录下，执行 **Codespaces: Rebuild Container**。
- **网页可以打开但连接中断**：刷新页面，并重新通过 GitHub 验证私有端口身份；确认 Codespace 未停止。
- **画面卡顿**：在左侧 Selkies 控制面板降低分辨率、帧率或画质，关闭不需要的标签页。

## 资料

- Chrome 镜像：https://docs.linuxserver.io/images/docker-chrome/
- Selkies 反向代理：https://docs.linuxserver.io/selkies/user-guide/reverse-proxy/
- Codespaces 端口转发：https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace
- 工作区持久化：https://docs.github.com/en/codespaces/developing-in-a-codespace/persisting-environment-variables-and-temporary-files
- 闲置计时：https://docs.github.com/en/codespaces/setting-your-user-preferences/setting-your-timeout-period-for-github-codespaces
- 计费：https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-codespaces/about-billing-for-github-codespaces
