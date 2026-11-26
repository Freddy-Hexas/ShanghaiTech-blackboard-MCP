# 认证与会话

## 登录方式

本项目使用独立的 Playwright Chromium 目录完成手动登录，不读取 Codex 侧边浏览器、Chrome、Edge 或 Dot 浏览器的 Cookie。安装步骤见 [README](../README.md#快速开始)。

在项目根目录、自己打开的交互式终端执行：

~~~powershell
.\.venv\Scripts\blackboard-probe.exe login
~~~

专用窗口打开后，完成学校正常登录流程，再回到终端按 Enter。程序通过本人课程注册接口验证身份，成功时输出 `state=authenticated`、`session_saved=true` 等状态。

~~~powershell
.\.venv\Scripts\blackboard-probe.exe status
~~~

`status` 会新建浏览器上下文，验证保存的会话是否仍然可用，不会要求你在聊天中提供凭证。CLI 成功时退出码为 0；认证、配置或网络等错误返回 2。MCP 的连接检查则使用 `get_connection_status`。

指定配置文件时，`--config` 必须放在子命令前：

~~~powershell
.\.venv\Scripts\blackboard-probe.exe --config config.local.toml login
~~~

登录 CLI 默认读取当前目录的 `config.local.toml`。MCP 服务另支持 `BLACKBOARD_CONFIG` 环境变量；两者必须指向同一份配置，才能使用同一个会话目录。

## 登录与后台读取的关系

登录和 CLI 状态检查使用 Playwright。MCP 工具调用使用保存的 Cookie 创建短生命周期 HTTPX 会话，不启动浏览器、不等待终端输入。

API、课程页面和附件下载共用同一个 HTTP Cookie jar。确认身份后保存服务端更新的 Cookie；401 或明确的登录页面会使查询停止并清除专用缓存，403 则报告资源权限不足，不使整个账号会话失效。

浏览器登录需要用户参与。即使 MCP 已启动，也不会在登录失效后自动弹窗、填写密码或重新登录。

## 保存位置与保护方式

默认目录为配置文件旁的 `.auth/browser-profile`：

- 仅使用空目录或本项目已标记的专用目录，不采用其他浏览器的现有 Profile。
- Windows 为目录设置当前用户访问权限；Cookie 缓存 `blackboard-session.dat` 使用当前用户 DPAPI 保护。
- POSIX 使用私有目录和文件权限，**没有实现额外的磁盘加密**。
- 专用 Cookie 缓存与完整 Blackboard origin 绑定，包括端口。其他 SSO 站点的 Cookie 不导出到该缓存。
- 浏览器 Profile 可能包含正常登录流程产生的浏览器数据，整个目录都应保持私有；不能把单个缓存文件的加密等同于整个目录加密。
- 不保存账号密码到项目配置，不在工具结果或终端中输出 Cookie 值。
- 同一个专用会话通过 `.session.lock` 协调访问，冲突返回 `session_busy`。等待正在进行的操作结束，不要强行删除锁。

会话目录、监控状态和本地配置都在 `.gitignore` 中。自定义目录时需要同步维护自己的排除规则。

## 会话能维持多久

“文件还在”和“学校仍认可会话”是两件事。

本地缓存可跨重启复用，直到被替换、删除或认证失败后清理。服务不改写 Cookie 的到期时间，不会给学校服务器签发新的有效期。

实际有效期取决于学校配置的超时和认证策略。项目没有测定一个可以保证的上科大会话时长；通用 Blackboard 文档中的默认值也不能直接代入学校部署。[Blackboard 会话管理](https://help.anthology.com/blackboard/administrator/en/authentication/session-management.html)

每 30 分钟查询一次可能影响空闲超时，但不能保证永久登录。返回 `login_required` 后，应手动重新执行登录命令。重新登录正常情况下不需要删除监控目录。

## 更换电脑、用户或执行宿主

Windows DPAPI 会话绑定当前用户环境，不能通过复制缓存把登录直接转移到其他机器或用户。在新环境安装项目，并完成一次新的手动登录。

Dot 云浏览器、连接的本机浏览器和本项目 Playwright Profile 各自独立。只在 Dot 的网页里登录，不会自动创建本项目所需的缓存。[Dot 的电脑与登录说明](https://learn.chatgpt.com/docs/dots/computers-and-apps)

自动任务必须能访问安装目录、配置文件、会话目录和学校网络；连接本机执行时，电脑及相关客户端也必须保持可用。

## 可选的内置连接检查

CLI 还保留一个有界 API 检查命令，便于确认部署环境：

~~~powershell
.\.venv\Scripts\blackboard-probe.exe probe
~~~

没有配置 `[probe].course_id` 时，只检查系统版本和本人注册分页；配置了实际课程及父目录 ID 后，还会检查相应课程、内容、子项和评分列。这是有限样本检查，不能代表整个学期都已完整扫描，也不决定 MCP 监控范围。

参考：[Playwright 认证状态](https://playwright.dev/python/docs/auth)、[Microsoft DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)。
