# 客户端与插件接入

## 推荐：Codex 项目级 STDIO

先按 [README](../README.md#快速开始) 完成安装和手动登录，然后执行：

**Windows · PowerShell**

~~~powershell
.\.venv\Scripts\python.exe scripts/configure_codex.py
~~~

**macOS · Terminal**

~~~bash
./.venv/bin/python scripts/configure_codex.py
~~~

脚本会生成 `.codex/config.toml`，记录当前 Python、配置文件和工作目录的绝对路径。启动超时为 30 秒，工具超时为 240 秒。

在 Codex 中打开并信任项目，再重新加载 MCP 或新建项目聊天。检查服务 `shanghaitech-blackboard` 是否提供 14 个工具，然后调用 `get_connection_status`。成功结果包含 `state="ok"`、`complete=true`、`data.authenticated=true`。项目级配置需要受信任项目，支持字段见 [官方 MCP 配置](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

配置脚本保留其他设置，也保留已有的同名 MCP 条目。它不会修改全局配置，不会自动安装插件或创建定时任务。生成的 TOML 使用 UTF-8 原样保存 Unicode 路径，支持中文、空格和 emoji；引号、反斜杠仍正确转义。

如果已有 STDIO 条目的 `command`、`args` 或 `cwd` 与当前运行环境不同，脚本会打印 `Launch settings differ`、差异字段及三项当前配置。项目搬家后，先在新位置重建虚拟环境并安装依赖，再用新环境运行脚本；将提示的三项替换到 `.codex/config.toml` 中已有的 `[mcp_servers.shanghaitech-blackboard]`，保留其他设置，不要追加重复段。重新加载 MCP 后再次调用 `get_connection_status`。已有 HTTP 条目不做本地启动路径比较。

STDIO 进程由客户端启动。无需另外启动常驻 HTTP 服务，也不必提前在终端运行 `blackboard-mcp`。

## 其他 STDIO 客户端

下面是通用启动参数示例，**所有路径都必须换成自己的安装位置**；不同客户端的外层配置格式可能不同：

**Windows**

~~~json
{
  "mcpServers": {
    "shanghaitech-blackboard": {
      "command": "C:/path/to/ShanghaiTech-blackoard-MCP/.venv/Scripts/python.exe",
      "args": [
        "-m",
        "blackboard_mcp.server",
        "--config",
        "C:/path/to/ShanghaiTech-blackoard-MCP/config.local.toml"
      ],
      "cwd": "C:/path/to/ShanghaiTech-blackoard-MCP"
    }
  }
}
~~~

**macOS**

~~~json
{
  "mcpServers": {
    "shanghaitech-blackboard": {
      "command": "/path/to/ShanghaiTech-blackoard-MCP/.venv/bin/python",
      "args": [
        "-m",
        "blackboard_mcp.server",
        "--config",
        "/path/to/ShanghaiTech-blackoard-MCP/config.local.toml"
      ],
      "cwd": "/path/to/ShanghaiTech-blackoard-MCP"
    }
  }
}
~~~

使用绝对 `--config` 可以避免客户端工作目录不同导致读取失败。客户端的单工具超时应高于配置的 `tool_timeout_seconds`。

服务还接受环境变量 `BLACKBOARD_CONFIG`；显式 `--config` 的优先级更高。使用可执行入口 `blackboard-mcp` 时，需确保客户端可以找到虚拟环境中的命令。

## 可选：本地插件包

源文件位于 `plugins/blackboard/`：

| 文件 | 用途 |
| --- | --- |
| `plugin.json` | 插件标识、版本和描述 |
| `mcp.json` | STDIO 服务声明 |
| `skills/blackboard-monitor/SKILL.md` | 监控、交付和确认规则 |

源包不捆绑 Python、学校 Cookie 或本机配置。直接使用源包时，需要预先安装 `blackboard-mcp`，并让插件进程收到 `BLACKBOARD_CONFIG` 的绝对路径；否则 `${PLUGIN_ROOT}` 工作目录中没有你的配置文件。

`configure_codex.py` 同时生成 `.local/plugins/blackboard/` 副本，将启动命令和配置路径写成当前机器的绝对路径，并在 `.local/.agents/plugins/marketplace.json` 建立目录索引。支持本地插件目录的客户端可添加该 `.local` 目录，再安装 `shanghaitech-blackboard`；具体安装入口以客户端版本为准。

直接 MCP 配置与插件调用相同服务，选择一种启用方式，避免重复工具。插件源文件更新后重新生成本地副本，并按客户端要求刷新安装。清单文件存在不代表客户端已经完成安装或官方目录已经收录。

## 实验性 Streamable HTTP

当前 HTTP 入口仅供本机接入验证：绑定 `127.0.0.1`，路径 `/mcp`，要求独立的 Bearer 令牌。**尚未补齐 Host/Origin 校验，也未实现 OAuth；当前配置不适合直接公开部署。** 见 [已知限制](limitations.md)。

在自己的终端运行：

**Windows · PowerShell**

~~~powershell
$env:BLACKBOARD_MCP_TOKEN = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
.\.venv\Scripts\blackboard-mcp.exe --config config.local.toml --transport http --port 8765
~~~

**macOS · Terminal**

~~~bash
export BLACKBOARD_MCP_TOKEN="$(./.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
./.venv/bin/blackboard-mcp --config config.local.toml --transport http --port 8765
~~~

客户端地址为 `http://127.0.0.1:8765/mcp`，需要 `Authorization: Bearer <token>`。令牌不足 32 个字符时服务拒绝启动；不要使用学校 Cookie 充当此令牌。

Codex 的 HTTP 配置示例，替代原有 STDIO 条目：

~~~toml
[mcp_servers.shanghaitech-blackboard]
url = "http://127.0.0.1:8765/mcp"
bearer_token_env_var = "BLACKBOARD_MCP_TOKEN"
startup_timeout_sec = 30
tool_timeout_sec = 240
~~~

客户端进程必须能读取与服务端相同的令牌环境变量。仅在一个 PowerShell/Terminal 窗口设置变量，不会把它自动传给已启动的桌面应用。令牌不能写入 Git、URL 或聊天。

HTTP MCP 鉴权与 Blackboard 会话独立：前者通过，不代表后者仍有效。不同 Windows 用户不能复用同一 DPAPI 会话。

## ChatGPT 网页

网页侧需要可用的远程 MCP 连接或 Secure MCP Tunnel，不能直接启动你电脑上的 STDIO 命令。

官方提供 **Plugins → Add custom MCP server → Tunnel** 的接入流程，要求相应工作区及隧道权限。可评估通过受控隧道接入本地服务，之后仍需验证鉴权、工具发现、调用权限及真实返回值。[官方隧道说明](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels#connect-from-chatgpt)

本项目没有自动创建隧道、远程部署或云端插件连接。固定 Bearer 的本机 HTTP 实现不是 OAuth 服务，也不能据此承诺所有 ChatGPT 接入方式都支持直接连接。

下载工具返回的是服务端路径，远程客户端不会自动获得附件字节。两个监控工具和下载工具会写入本地状态；只允许读工具的客户端无法完成全部流程。

## Automation 与 Dot

配置定时任务前，先在目标执行环境中完成一次手动工具调用。调度器需要：

- 可访问 MCP 服务、同一份配置和学校网络。
- 有效的学校会话，以及相应本地文件访问权限。
- 允许监控工具写入本地基线。
- 保存通知交付证据，以正确执行批次确认。

服务没有内部定时器，也不会自行发送通知。可复用提示词见 [监控协议](monitoring.md)。

Dot 拥有独立云电脑和浏览器；本地浏览器登录不会转移到云电脑，云浏览器登录也不会导入此项目的专用 Profile。使用连接的本机电脑时，要保持该电脑在线且应用打开；实际可用的工具还取决于插件、账号和执行环境。[Dot 官方说明](https://learn.chatgpt.com/docs/dots/computers-and-apps)

每 30 分钟执行一次可以作为监控频率，但不能保证学校会话不过期。出现 `login_required` 后需要重新手动登录。
