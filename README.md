# ShanghaiTech Blackboard MCP

> **让 AI Agent 接入 Blackboard，从获取作业到辅助完成，少一些来回切换。**

“看看这周有哪些作业，把附件下载好，带我分析一下题目。”接入 ShanghaiTech Blackboard MCP 后，你可以让 Codex 等 AI Agent 直接读取课程和作业要求、下载附件，再结合 Agent 自身的阅读、推理与编程能力，讲解题目、辅助解答或编写代码。配合定时任务，还能自动跟踪新作业、截止时间变化和新增课件，把反复打开网页查找信息的步骤交给自动化。

本项目提供连接上海科技大学 Blackboard 的工具；资料获取由 MCP 完成，后续的解题与学习辅助由所连接的 Agent 承接。

当前版本：**0.3.1**。推荐使用 **Codex/ChatGPT**。项目包含 MCP 服务和可选插件包。

## 适配进度

- [x] ChatGPT/Codex
- [ ] Claude Code
- [ ] DeepSeek Desktop

## 功能概览

- **课程查询**：列出注册课程、搜索课程、查看课程详情及明确的学期 ID。
- **作业与截止时间**：读取作业说明、系统截止时间。
- **课件查询**：浏览内容树，查看文档、文件、链接及支持的附件元数据。
- **作业附件下载**：保存教师发布的附件，文件名使用“课程名-原附件名”，返回本地路径和 SHA-256。
- **变化监控**：发现新作业、截止时间变化、新课件和课件附件变化，使用本地基线和批次确认避免静默漏报。
- **本地会话复用**：在专用浏览器登录一次，后续 MCP 调用复用保存的会话；过期后手动重新登录。

对 Blackboard 的数据操作均为 GET。服务不提交作业、不修改课程、不读取个人成绩或提交记录；监控和下载会写入本地文件。

## 安装前准备

需要：

1. Python **3.11 或以上**，以及 Git。
2. 能正常登录 Blackboard 的个人账号。
3. 能访问学校 Blackboard 的网络环境。
4. 支持启动本地 STDIO MCP 的客户端，例如 Codex。

下文并列提供 **Windows PowerShell** 和 **macOS Terminal（zsh/bash）** 命令。获取项目后，其余命令均在项目根目录执行。先确认 Windows 的 `python --version` 或 macOS 的 `python3 --version` 为 **3.11 或以上**；若默认命令版本较旧，请先安装新版 Python，并使用对应解释器创建虚拟环境，例如 macOS 的 `python3.11 -m venv .venv`。

Windows 的虚拟环境命令位于 `.venv/Scripts/`，macOS 位于 `.venv/bin/`，无需激活环境。项目路径可以包含中文、空格或 emoji；在终端输入完整路径时加引号，例如 macOS 的 `cd "/path/to/课程资料 📚/ShanghaiTech-blackoard-MCP"`。参见 [Python 虚拟环境说明](https://docs.python.org/3/library/venv.html)。

## 快速开始

### 1. 获取项目并安装依赖

**Windows · PowerShell**

~~~powershell
git clone https://github.com/Freddy-Hexas/ShanghaiTech-blackoard-MCP.git
Set-Location ShanghaiTech-blackoard-MCP

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m playwright install chromium

if (-not (Test-Path -LiteralPath config.local.toml)) {
    Copy-Item -LiteralPath config.example.toml -Destination config.local.toml
}
~~~

**macOS · Terminal**

~~~bash
git clone https://github.com/Freddy-Hexas/ShanghaiTech-blackoard-MCP.git
cd ShanghaiTech-blackoard-MCP

python3 -m venv .venv
./.venv/bin/python -m pip install -e .
./.venv/bin/python -m playwright install chromium

if [ ! -e config.local.toml ]; then
    cp config.example.toml config.local.toml
fi
~~~

`pip install -e .` 安装 Python 依赖后，仍需**单独执行 Chromium 安装命令**。已有 Chrome 或 Safari 不能替代本项目需要的 Playwright Chromium；更新 Playwright 后也可能需要重新安装配套浏览器。[Playwright 浏览器安装说明](https://playwright.dev/python/docs/browsers#install-browsers)

仓库 URL 中的名称为 `ShanghaiTech-blackoard-MCP`，Python 包名为 `shanghaitech-blackboard-mcp`。

不必激活虚拟环境；上面的命令直接使用项目自己的 Python。`config.local.toml` 是你的本机配置，已被 Git 忽略。已有环境或配置时，跳过相应创建步骤。

### 2. 在专用浏览器中登录

在自己打开的交互式终端中运行；macOS 请打开 **Terminal（终端）** 并进入项目目录：

**Windows · PowerShell**

~~~powershell
.\.venv\Scripts\blackboard-probe.exe login
~~~

**macOS · Terminal**

~~~bash
./.venv/bin/blackboard-probe login
~~~

程序会打开专用 Chromium 窗口。按学校正常流程完成登录，确认已进入 Blackboard 后，回到终端按 Enter。成功结果包含：

~~~json
{
  "state": "authenticated",
  "session_saved": true
}
~~~

实际输出还包含注册记录数量等字段。可以再运行：

**Windows · PowerShell**

~~~powershell
.\.venv\Scripts\blackboard-probe.exe status
~~~

**macOS · Terminal**

~~~bash
./.venv/bin/blackboard-probe status
~~~

`blackboard-probe` 是登录和连接检查入口，名称中的 probe 不代表需要额外的调试脚本。登录不通过 MCP 工具进行，也不需要把密码或 Cookie 发到聊天里。

**在 Codex 侧边浏览器、普通浏览器或 Dot 浏览器中已登录，不等于此 MCP 已登录。** 本项目使用独立的浏览器目录和会话文件。

### 3. 生成 Codex 配置

**Windows · PowerShell**

~~~powershell
.\.venv\Scripts\python.exe scripts/configure_codex.py
~~~

**macOS · Terminal**

~~~bash
./.venv/bin/python scripts/configure_codex.py
~~~

脚本生成：

| 文件或目录 | 用途 |
| --- | --- |
| `.codex/config.toml` | 项目级 MCP 启动配置，包含当前 Python 和配置文件的绝对路径 |
| `.local/plugins/blackboard/` | 绑定当前运行环境的插件副本 |
| `.local/.agents/plugins/marketplace.json` | 本地插件目录索引 |

这些文件均留在本机。脚本保留其他 Codex 设置；如果同名 MCP 条目已经存在，也会保留该条目。对于已有 STDIO 配置，脚本会比较 `command`、`args` 和 `cwd` 与当前运行环境：不同则列出差异字段和当前路径的替换内容，方便项目搬家后刷新；不会自动覆盖自定义设置。虚拟环境不适合直接搬移，迁移项目后应先在新位置重建 `.venv`、安装依赖，再使用新环境运行配置脚本。[Python 虚拟环境迁移说明](https://docs.python.org/3/library/venv.html#how-venvs-work)

随后在 Codex 中打开并信任这个项目，重新加载 MCP 或新建项目聊天。服务名是 `shanghaitech-blackboard`，应能发现 **14 个工具**。项目级配置只在受信任项目中加载，配置格式见 [Codex 官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

登录、生成配置并在 Codex 中信任项目后，先调用连接检查工具；可以在聊天中说：

> 请调用 Blackboard MCP 的 get_connection_status 检查连接，成功后再列出我的课程及其学期 ID。

MCP 连接成功时，`get_connection_status` 的结果应包含 `state="ok"`、`complete=true` 和 `data.authenticated=true`。

STDIO 服务由客户端启动，不需要另外开一个终端长期运行。手动运行 `blackboard-mcp` 后等待输入，是它在等待 MCP 协议消息。

### 4. 设置默认学期

通过 `list_courses` 或 `get_course` 查看实际的 `term_id`，然后在 `config.local.toml` 已有的 `[blackboard]` 段中设置：

~~~toml
[blackboard]
base_url = "https://elearning.shanghaitech.edu.cn:8443"
timezone = "Asia/Shanghai"
current_term_id = "_110_1" # 仅为格式示例，务必替换为自己的实际学期 ID
~~~

修改后重新加载 MCP 服务，使新配置生效。不要重复添加同名 TOML 段。

- `list_courses()` 不传学期时列出本人注册课程；传 `term_id` 才按学期筛选。
- 跨课程的作业、截止时间查询和监控，默认使用 `current_term_id`。
- 也可以在调用时指定某个课程或学期，无需设置默认学期。
- 学期没有配置、调用又没有明确范围时，服务会报告 `scope_required`。
- 课程内部 ID、课程代码和课程名称不是同一字段；同名教学班也可能有不同 ID。

## 日常使用示例

完成配置后，可直接在聊天中提出需求：

> 查找“某课程”的课程信息，列出它的作业和截止时间。

> 看一下本学期未来 7 天有哪些截止时间；系统日期和作业正文冲突时分别列出。

> 列出某门课程的课件和附件名称。

> 找到某门课程某次作业，把附件下载下来，告诉我保存位置。

涉及同名课程或作业时，先用查询工具确认准确 ID。服务只能读取当前账号有权限访问的课程。

截止时间工具会区分未来到期、已过截止时间但提交状态未知、日期缺失或待核实的项目。**已过截止时间不代表没有提交。**

## MCP 工具一览

下表中的 ID 应来自查询结果。所有工具都返回统一结果结构，调用方需要检查 `state`、`complete` 和 `warnings`。

| 工具 | 主要参数 | 作用 |
| --- | --- | --- |
| `get_connection_status` | 无 | 检查真实登录状态及默认学期 |
| `list_courses` | 可选 `term_id` | 列出注册课程，可按明确学期筛选 |
| `search_courses` | `query` | 按课程名或代码搜索 |
| `get_course` | `course_id` | 读取课程详情 |
| `list_course_contents` | `course_id` | 读取顶层内容 |
| `get_content_tree` | `course_id` | 在预算内递归读取内容树 |
| `list_assignments` | 可选 `course_id` 或 `term_id` | 读取作业及匹配的评分列定义 |
| `get_assignment` | `course_id`、`content_id` | 读取单份作业详情与日期证据 |
| `get_upcoming_deadlines` | `days`，可选课程或学期 | 读取未来 N 天截止时间，默认 7 天 |
| `list_materials` | `course_id` | 读取课件及附件元数据，不下载文件 |
| `list_assignment_attachments` | `course_id`、`content_id` | 列出教师发布的作业附件 |
| `download_assignment_attachments` | 课程、作业 ID，可选 `attachment_ids` | 下载全部或选定的作业附件 |
| `check_updates` | `watcher_id`，可选 `course_ids` 或 `term_id` | 比较快照，返回变化或待确认批次 |
| `acknowledge_updates` | `watcher_id`、`batch_token` | 确认已成功处理的变化批次 |

下载工具和两个监控工具会写本地文件，因此不是只读工具。客户端的工具权限策略必须允许相应调用。

## 作业附件下载

默认保存结构：

~~~text
downloads/
  <账户标识>/
    <课程ID>/
      <作业ID>/
        课程名-作业.pdf
~~~

目录用于隔离账户、课程和作业；实际文件名前缀使用课程名称。文件名中的非法字符会被处理，长名称会被截短并保留扩展名。

- 同名且内容相同：校验哈希后复用已有文件，返回 `reused=true`。
- 同名但内容不同：使用 `课程名-作业 (2).pdf` 等编号，不覆盖已有文件。
- `file_name` 是原附件名，`saved_file_name` 是实际保存的文件名。
- 成功文件包含 `path`、`bytes`、`sha256`、`media_type` 等信息。
- 单次部分失败时保留已完成文件，返回失败列表和不完整状态。

默认单文件上限 **50 MiB**、单次最多 **20 个文件**、合计读取上限 **200 MiB**，可在配置中调整。

文件位于 **MCP 服务运行的电脑**。本机 Codex 可以继续读取该路径；远程 ChatGPT 或 Dot 收到路径，并不意味着收到了文件内容。当前没有远程文件交付接口。下载不会自动解压或执行附件。

当前支持 Classic 作业页面中可识别的同源 WebDAV 教师附件。普通课件目前提供元数据查询；外部云盘及不支持的页面布局会明确报告限制。详细说明见 [作业附件下载](docs/attachments.md)。

## 定时监控

MCP 负责“读取、比较、记录和确认”，**运行频率及通知由 Codex Automation、Dot 或其他调度器负责**。安装项目不会自动创建定时任务。

### 正确的调用流程

1. 选择固定 `watcher_id`，并指定 `course_ids` 或 `term_id`；都省略时使用配置的默认学期。
2. 调用 `check_updates`。首次完整扫描返回 `baseline_created`，建立基线，不把历史内容当成新通知。
3. 后续扫描发现变化时返回 `pending_acknowledgement`、事件列表和 `batch_token`。
4. 成功处理并确认通知已交付后，调用 `acknowledge_updates`。
5. 下次运行继续检查；若先确认的是旧批次，应随后再调用一次 `check_updates` 获取新变化。

未确认的批次会重复返回，`cached=true`，且不会扫描更晚的变化。如果通知就是当前聊天的最终回复，应在下一次运行确认该回复确实存在后，再确认相应批次。

失败或不完整扫描不会推进基线。只有成功、完整、非缓存的扫描返回 `no_changes`，才能判断本次没有检测到变化。

主要事件包括新作业、截止时间变化、新课件和新增课件附件，也包括部分元数据修改或移除。**作业附件新增/替换、个人成绩和提交状态不在自动监控范围内。**

### 可用于创建定时任务的提示词

> 使用 Blackboard MCP 检查当前配置学期的新作业、截止时间变化和新增课件，watcher_id 固定为 semester-coursewatch。若能从历史确认上轮批次已成功交付，先确认该批次，再进行本次检查。首次运行报告基线已建立；有变化时列出课程、标题、来源链接和上海时区时间。截止时间有冲突时分别说明。失败或不完整时报告原因，不要说没有更新。若通过最终回复交付结果，把批次 token 保存在回复中供下轮核实；没有交付证据就保持待确认。

创建任务时再指定需要的频率，例如每 30 分钟。执行宿主必须在线、能访问学校网络，并且能读取相同配置和会话。新学期或监控范围变化时，使用新的 watcher。

完整事件语义与确认规则见 [监控协议](docs/monitoring.md)。

## 登录一次能维持多久？

本地会话文件可以跨进程、跨重启保存，**实际登录有效期由学校服务器决定，目前还未测得Cookie的时长**。

定时访问可能影响服务器的空闲计时，但每 30 分钟查询一次不能保证 Cookie 永不过期。退出登录、服务端超时或认证策略变化都可能使会话失效。本项目不修改 Cookie 的到期时间，也没有自动重新登录功能。

看到 `login_required` 时，重新运行：

**Windows · PowerShell**

~~~powershell
.\.venv\Scripts\blackboard-probe.exe login
~~~

**macOS · Terminal**

~~~bash
./.venv/bin/blackboard-probe login
~~~

Windows 下，专用 Cookie 缓存使用当前用户 DPAPI 保护，不能把文件复制到另一台电脑或另一用户下就当作已登录。换执行环境时，应在新环境完成自己的登录流程。

macOS 使用私有目录和文件权限保护本地缓存，没有额外实现 DPAPI 或磁盘加密。

详见 [认证与会话](docs/authentication.md)。

## 配置说明

完整模板见 [config.example.toml](config.example.toml)。相对路径均以**配置文件所在目录**为基准。

| 配置段 | 关键设置 | 默认值或作用 |
| --- | --- | --- |
| `[blackboard]` | `base_url`、`timezone`、`current_term_id` | 学校 HTTPS 地址、上海时区、需自行确认的默认学期 |
| `[auth]` | `profile_dir` | `.auth/browser-profile`，专用登录和监控目录 |
| `[downloads]` | `download_dir` | `downloads` |
| `[downloads]` | `max_download_bytes` | `52428800`，50 MiB |
| `[downloads]` | `max_total_download_bytes`、`max_download_files` | `209715200`、`20` |
| `[network]` | `timeout_seconds`、`max_retries`、`min_interval_seconds` | 单次请求 15 秒、最多重试 2 次、间隔 0.3 秒 |
| `[network]` | `max_pages`、`max_items` | 分页和集合数量上限：20、2000 |
| `[limits]` | `max_depth`、`max_nodes`、`max_courses`、`max_requests` | 12、1000、20、300 |
| `[limits]` | `tool_timeout_seconds` | 单工具预算 180 秒；生成的 Codex 配置允许 240 秒 |
| `[probe]` | 可选课程及父目录 ID | 仅用于 CLI 连接检查，不决定 MCP 的默认学期 |

这些限制是客户端的保守预算，不是学校公布的访问配额。达到预算会报告不完整结果。

MCP 服务支持 `--config`；未指定时读取环境变量 `BLACKBOARD_CONFIG`，再回退到 `config.local.toml`。登录 CLI 默认使用当前目录的 `config.local.toml`；指定其他配置时把参数放在子命令前：

**Windows · PowerShell**

~~~powershell
.\.venv\Scripts\blackboard-probe.exe --config config.local.toml login
~~~

**macOS · Terminal**

~~~bash
./.venv/bin/blackboard-probe --config config.local.toml login
~~~

## 常见问题

| 现象 | 处理方法 |
| --- | --- |
| 没有出现 MCP 工具 | 确认打开并信任了项目，运行配置脚本，检查 Python 绝对路径，再重新加载 MCP |
| 登录浏览器无法启动 | 单独安装 Chromium：Windows 用 `.\.venv\Scripts\python.exe -m playwright install chromium`；macOS 用 `./.venv/bin/python -m playwright install chromium` |
| macOS 的 Python 版本低于 3.11 | 安装 Python 3.11+，用对应解释器创建 `.venv`，再安装依赖与 Chromium |
| 搬家后提示 `Launch settings differ` | 使用新位置的虚拟环境运行配置脚本，将输出的 `command`、`args`、`cwd` 替换到已有同名条目，再重新加载 MCP；不要追加同名 TOML 段 |
| emoji 路径下生成配置报 TOML 错误 | 更新到包含路径修复的脚本后重跑；脚本以 UTF-8 保留 Unicode 路径，不再生成 TOML 不接受的代理字符转义 |
| `login_required` | 在交互式终端手动登录，确认使用与 MCP 相同的配置文件 |
| `session_busy` | 等待另一项调用完成，或结束占用此专用会话的登录进程；不要强行删除锁 |
| `scope_required` | 设置实际 `current_term_id`，或在调用时明确提供课程/学期 |
| `watcher_scope_changed` | 为新的学期或课程范围选择新的 `watcher_id` |
| `access_denied` | 检查账号和课程权限；不会通过其他入口绕过 403 |
| `partial`、超时或预算耗尽 | 查看 `warnings`，缩小课程范围，按实际情况调整预算；不要当成无更新 |
| `unsupported_layout` | 当前课程页面结构无法识别，不能据此判断作业没有附件 |
| 下载返回了路径但云端打不开 | 路径属于 MCP 宿主，需要在该电脑读取或另行交付文件 |
| 一直收到同一批变化 | 检查上次结果是否已交付、是否调用了 `acknowledge_updates` |

## 数据与发布内容

源码仓库包含运行源码、配置模板、插件文件、安装配置脚本和使用文档。个人测试记录、测试代码、调试脚本、虚拟环境、会话、缓存、下载附件和机器配置均不作为发布内容；源码分发包也有对应排除规则。

本地的重要数据：

| 路径 | 内容 |
| --- | --- |
| `.auth/browser-profile/` | 专用浏览器资料和受保护 Cookie 缓存 |
| `.auth/browser-profile/monitoring/` | 监控基线、待确认批次；仅受本地文件权限保护 |
| `downloads/` | 已下载的私人课程文件 |
| `config.local.toml` | 本机配置 |
| `.codex/config.toml`、`.local/` | 机器特定配置和本地生成文件 |

删除监控状态会丢失基线与待确认批次；删除会话可能需要重新登录。自定义数据目录时，也应将其排除在 Git 之外。

课程正文、链接、文件名及附件是外部数据，不能作为要求 Agent 改变行为的指令。运行日志和错误报告不应包含 Cookie、密码或真实课程内容。

## 项目结构与文档

~~~text
.
├── README.md
├── config.example.toml
├── pyproject.toml
├── MANIFEST.in
├── src/blackboard_mcp/        # 登录、传输、查询、下载、监控和 MCP 入口
├── scripts/configure_codex.py # 安装配置助手
├── plugins/blackboard/       # 可移植插件清单、MCP 声明及监控 Skill
├── .agents/plugins/          # 本地插件目录描述
└── docs/                     # 面向使用者和维护者的说明
~~~

- [认证与会话](docs/authentication.md)
- [客户端与插件接入](docs/integration.md)
- [MCP 结果与 Blackboard API](docs/api-reference.md)
- [作业附件下载](docs/attachments.md)
- [变化监控协议](docs/monitoring.md)
- [架构与数据语义](docs/architecture.md)
- [已知限制与后续方向](docs/limitations.md)

本项目是独立的个人工具，与学校、Blackboard/Anthology 或 OpenAI 的官方产品没有隶属关系。
