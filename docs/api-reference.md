# MCP 结果与 Blackboard API

工具清单和使用入口见 [README](../README.md#mcp-工具一览)。本页说明结果语义及服务使用的上游接口，适合编写调用方或排查兼容性。

## MCP 统一返回结构

~~~json
{
  "source": "blackboard",
  "fetched_at": "2026-01-01T00:00:00+00:00",
  "cached": false,
  "complete": true,
  "state": "ok",
  "warnings": [],
  "data": {}
}
~~~

时间为示例，`data` 的具体字段取决于工具。

| 字段 | 含义 |
| --- | --- |
| `source` | 数据来源，固定为 `blackboard` |
| `fetched_at` | 本次结果时间；重放待确认批次时为原快照时间 |
| `cached` | 是否重放已有的监控批次 |
| `complete` | 声明范围内的操作是否完整完成 |
| `state` | `ok`、`partial` 或明确错误码 |
| `warnings` | 日期歧义、缺失字段、权限或预算等提示 |
| `data` | 工具数据、监控状态或错误详情 |

当前业务错误仍采用普通 MCP 结果外壳，协议层 `isError` 未完整反映失败。调用方必须检查 `state` 和 `complete`，不能只看调用是否抛错。具体问题见 [已知限制](limitations.md)。

## 主要数据字段

| 工具类别 | 主要数据 |
| --- | --- |
| 连接状态 | `authenticated`、`current_term_id`、`session_lifetime` |
| 课程查询 | `id`、`course_code`、`name`、`term_id`、来源 `url` |
| 内容 | `course_id`、内容 ID、类型、父目录、说明和来源链接 |
| 作业 | 内容元数据、关联评分列、截止时间证据及冲突；个人成绩/提交状态未知 |
| 截止时间 | `assignments`、`past_due_submission_unknown`、`undated_or_unresolved` |
| 课件 | `materials`，各项包含 `attachments` 和 `attachments_state` |
| 附件列表 | `attachments`，含不透明附件 `id` 和原始 `file_name` |
| 附件下载 | `files`、`failed`；成功文件含 `path`、`saved_file_name`、`bytes`、`sha256` |
| 监控 | `monitor_state`、范围、快照时间、事件、`batch_token`；字段依状态而异 |

内部课程、内容、学期 ID 采用 `_数字_数字` 格式。附件 ID 与监控 token 应原样使用工具返回值，不要从文件名、链接或课程代码中猜测。

`list_assignments` / `get_upcoming_deadlines` 的 `course_id` 与 `term_id` 互斥。`check_updates` 的 `course_ids` 与 `term_id` 互斥。默认范围见 [配置说明](../README.md#配置说明)。

## 上游 API 路径

默认基址为 `https://elearning.shanghaitech.edu.cn:8443`。下列 REST 路径的共同前缀为 `/learn/api/public/v1`：

| GET 路径 | 用途 |
| --- | --- |
| `/system/version` | CLI 有界连接检查中的版本信息 |
| `/users/me/courses` | 验证登录、分页读取本人注册课程并作为授权依据 |
| `/courses/{courseId}` | 课程名称、代码、明确学期等信息 |
| `/courses/{courseId}/contents` | 顶层内容 |
| `/courses/{courseId}/contents/{contentId}/children` | 子内容分页与递归树 |
| `/courses/{courseId}/gradebook/columns` | 作业关联和评分列定义，不是个人实际分数 |
| `/courses/{courseId}/contents/{contentId}/attachments` | 支持的文档/文件类型的附件元数据 |

项目兼容目标包含 Learn `3300.0.1` 的 Classic 课程。旧版本不能假设支持较新 Blackboard 文档中的所有接口：当前不提供公告、个人 Grade/Attempt 查询或普通课件二进制下载。

Classic 作业附件另走经过授权和精确定位的页面路径：

- `GET /webapps/blackboard/content/listContent.jsp`：读取父目录中该作业条目。
- `GET /bbcswebdav/...`：下载与该作业和资源标识匹配的同源教师附件。

此兼容路径不访问作业提交或尝试页面。详见 [附件说明](attachments.md)。

## 内容类型

| 类型 | 处理方式 |
| --- | --- |
| `resource/x-bb-folder` | 根据 `hasChildren` 展开，受深度和节点预算限制 |
| `resource/x-bb-assignment` | 作为作业，按内容 ID 关联全部匹配评分列 |
| `resource/x-bb-document` | 作为课件，并尝试读取支持的附件元数据 |
| `resource/x-bb-file` | 作为课件，并尝试读取支持的附件元数据 |
| `resource/x-bb-externallink` | 保留链接元数据，不跟随外部链接下载 |

未知类型保留原始标识；影响完整监控时显式报告不完整，避免静默漏报。

## 上游响应与错误

| 情况 | 处理 |
| --- | --- |
| 2xx + 合法 JSON | 解析结果，集合继续校验 `results` 和分页 |
| 登录 HTML / 登录重定向 / 401 | 报告 `login_required`，停止并按会话流程处理缓存 |
| 其他非预期 HTML 或重定向 | 显式报错，不当作有效 API 数据 |
| 403 | `access_denied`，不重试或绕过资源权限 |
| 400 / 404 | 明确报告请求或资源问题，不视为空列表 |
| 429 | 有限退避，过长的 Retry-After 不无限等待 |
| 5xx / 网络故障 | JSON API 有限重试；耗尽后报告失败 |
| 预算耗尽 / 分页异常 / 部分资源失败 | 不完整结果；监控不推进基线 |

JSON API 不自动跟随重定向。附件下载可逐跳跟随符合规则的同源同资源重定向；并非任意 URL 跳转。TLS 证书校验始终开启。

上游资料：[Blackboard REST 文档](https://docs.blackboard.com/docs/blackboard/rest-apis/start-here)、[官方附件访问示例](https://docs.blackboard.com/docs/blackboard/rest-apis/demo-code/curl-attach-demo)。上游文档不替代目标服务器的实际版本和账号权限。
