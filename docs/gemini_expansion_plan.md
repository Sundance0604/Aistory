# Gemini 对话同步扩展设计（历史路线图）

本文保留早期路线图和未来媒体能力设想。基础 Gemini 同步、统一统计、生命周期、主题颜色与平台合并视图现已实现；当前准确行为请阅读 [gemini_analytics_implementation.md](gemini_analytics_implementation.md)。

## 1. 调研结论

参考项目 [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API) 提供了一个面向 Gemini Web 的异步 Python 客户端。与本项目相关的能力包括：

- 使用 `GeminiClient` 初始化会话；
- 通过 `list_chats` 获取近期会话列表；
- 通过 `read_chat` 按会话 ID 读取历史，返回的回合顺序为从新到旧；
- 自动刷新 Gemini Cookie，并可通过 `GEMINI_COOKIE_PATH` 指定持久化位置；
- 动态发现当前账号可用的模型；
- 输出可区分普通文本、思考内容、图片、视频和音频等类型；
- Python 要求为 3.11 或更高版本。

该项目使用 Gemini Web 的逆向接口，并非 Google 官方 API。认证通常依赖 `__Secure-1PSID` 和 `__Secure-1PSIDTS` Cookie；Chromium 的设备绑定会话凭据可能导致 Cookie 生命周期较短。它采用 **AGPL-3.0** 许可证；VisualGPT 发布仓库采用 GPL-3.0，并将 `gemini-webapi` 保持为可选外部依赖，不复制或内嵌其源码。

## 2. 设计原则

1. **平台无关的核心模型**：分析、主题分类和前端不应依赖 ChatGPT 或 Gemini 的原始响应结构。
2. **适配器隔离**：每个平台的登录、列表、分页、重试和解析代码位于独立模块。
3. **认证信息隔离**：账号元数据可以进入统一 JSON 配置，Cookie 值本身不得进入数据库、日志或 Git。
4. **可验证的增量策略**：只有数据源明确保证更新时间倒序且更新时间稳定时，才能使用“首个 unchanged 后停止”。
5. **原始数据可追溯**：始终先保存平台原始响应，再生成统一结构；解析器升级后可以离线重建。
6. **串行账号同步**：同一平台或不同平台的账号默认依次执行，避免 Cookie、浏览器资料与限速相互影响。

## 3. 建议的数据模型升级

当前账号隔离键为 `account_id + remote_id`。接入 Gemini 前建议升级为：

```text
provider + account_id + remote_id
```

建议为账号增加 `provider`、`auth_method` 和 `enabled` 字段；为对话增加 `provider`、`provider_metadata_json`，并把唯一索引调整为 `(provider, account_id, remote_id)`。

统一消息模型继续保留：

- `role`
- `visible_text`
- `visible_tokens`
- `created_at`
- `model`
- `content_type`
- `is_active_branch`
- `has_attachment`
- `provider_metadata_json`

Gemini 的 thoughts、工具数据和媒体描述不得默认计入可见 Token。媒体本身只记录元数据；是否下载二进制资源应由独立、显式的配置控制。

## 4. 数据源接口

建议把当前 `ChatGPTWebSource` 抽象为统一协议：

```python
class ConversationSource(Protocol):
    provider: str
    capabilities: SourceCapabilities

    def list_conversations(self, checkpoint: SyncCheckpoint | None) -> Page: ...
    def fetch_conversation(self, item: RemoteConversation) -> dict: ...
    def normalize(self, raw: dict) -> NormalizedConversation: ...
    def close(self) -> None: ...
```

`SourceCapabilities` 至少声明：

- 是否按更新时间稳定倒序；
- 是否提供可靠的 `updated_at`；
- 是否支持分页游标；
- 是否支持完整历史；
- 是否包含分支或候选回复；
- 是否支持附件元数据；
- 是否允许提前停止。

这样，“遇到首个 unchanged 后停止”不会被错误地套用到 Gemini。参考库只明确描述了“近期会话列表”，尚不能证明它能分页获得账号的完整历史，也不能证明列表顺序和更新时间适合使用当前 ChatGPT 的堆式同步算法。

## 5. Gemini 适配器草案

建议模块布局：

```text
gpt_activity/
└── providers/
    ├── base.py
    ├── chatgpt_web.py
    └── gemini_web.py
```

`GeminiWebSource` 的职责：

1. 从账号配置读取 Cookie 缓存路径或浏览器导入方式；
2. 初始化异步客户端；
3. 获取近期会话列表；
4. 逐个读取会话历史；
5. 把从新到旧的回合转换为统一的从旧到新顺序；
6. 区分用户文本、模型可见文本、thoughts、图片、音视频和附件；
7. 生成稳定的消息 ID、内容哈希和原始快照；
8. 正常关闭客户端并持久化刷新后的 Cookie。

当前后端同步为同步式 Playwright 流程，而参考客户端基于 `asyncio`。不要在已有事件循环里直接调用 `asyncio.run`。可选方案：

- 将所有 provider 统一为异步接口，并让后台作业使用专用事件循环；或
- 首期用独立 worker/subprocess 封装 Gemini 异步客户端，通过 JSON Lines 与主进程通信。

第二种方式也更适合做依赖和许可证隔离。

## 6. 认证与配置

建议配置只保存非秘密信息：

```json
{
  "id": "gemini-personal",
  "provider": "gemini",
  "name": "Gemini 个人账号",
  "auth_method": "cookie_cache",
  "cookie_path": "secrets/gemini/personal",
  "enabled": true
}
```

Cookie 内容应来自运行时环境变量、Git 忽略的秘密文件、操作系统凭据存储或可审计的浏览器 Cookie 导入。设置 API 只能返回 `configured: true/false`，不得返回 Cookie。日志中也必须过滤 Cookie、授权请求头和原始 RPC 参数。

## 7. 增量同步策略

Gemini 首期建议使用保守策略：

1. 获取当前可见的近期会话列表；
2. 对每个会话读取轻量标识；
3. 若没有可靠更新时间，则比较远端列表指纹和本地内容哈希；
4. 只抓取新 ID，定期抽样重新抓取最近的若干旧会话；
5. 提供显式 `--full-index`；
6. 在确认排序和更新时间语义前，禁用“首个 unchanged 即停止”。

需要通过真实账号验证：

- `list_chats` 是否分页；
- 能否获得完整历史，而不只是近期记录；
- 编辑旧对话后是否移动到列表顶部；
- 标题、模型和时间戳字段是否稳定；
- 删除或归档对话如何表现；
- Gemini Apps Activity 关闭时历史是否仍可读取。

## 8. 数据规范化

Gemini `read_chat` 返回的回合从新到旧，写入数据库前必须反转。

| Gemini 数据 | 统一模型 |
|---|---|
| 用户回合文本 | `role=user`, `visible_text` |
| 模型回答文本 | `role=assistant`, `visible_text` |
| thoughts | 保留在元数据，默认不可见且不计 Token |
| 图片、视频、音频 | `content_type` 加附件元数据 |
| 模型名称 | `model` |
| 会话 ID | `remote_id` |

如果 Gemini 返回多个候选回答，需要选择当前展示候选作为活动分支，同时保留其他候选的原始数据；不能把所有候选回答相加后计入 Token。

## 9. 错误处理与限速

- 对 401/403 或未认证状态立即停止当前账号，不自动尝试其他 Cookie。
- 对 429 和服务端错误使用指数退避与随机抖动。
- 每个账号维护独立 checkpoint 和上次同步状态。
- 单账号失败不阻止队列中的下一个账号，但总任务标记为 partial。
- Cookie 刷新失败时提示用户重新登录，不在 UI 中显示 Cookie 内容。
- 给 Gemini 单独设置请求间隔，不复用 ChatGPT 的限速参数。

## 10. 活动时间窗口与完整日期浏览

当前“活动”界面只展示有限数量的周期，日期和月份标签也会因可视区域不足而被截断。未来改造要求：

- 日、周、月视图都保留完整数据，不再通过 `slice` 只显示最后若干项；
- 图表区域使用内置滑动窗口，横向浏览时间、纵向浏览明细，不依赖整个页面滚动；
- 初始窗口定位到最近活动周期，并提供“最早”“最近”“今天”快捷定位；
- 鼠标滚轮、触控板、拖拽和键盘方向键都可以移动窗口；
- 日期、月份和年份标题随窗口移动保持可见，不能省略到无法判断时间；
- 数据量较大时使用虚拟化渲染，只渲染视口及缓冲区内容；
- 选择热力格或柱状单元后显示该周期的提示数、输入 Token、输出 Token、总 Token 和活跃对话数；
- 切换平台或账号后保持相同的时间范围，便于对比。

后端继续返回完整时间序列；滑动窗口主要是前端显示策略，不应为了界面性能丢弃历史数据。

## 11. 个性化主题词

主题分析需要支持用户维护自己的分析词表，使分类模型优先使用用户熟悉的概念，而不是不断创建近义主题。

建议配置结构：

```json
{
  "topic_preferences": {
    "preferred_topics": [
      {
        "name": "Agent 工程",
        "aliases": ["智能体", "Agentic Workflow"],
        "description": "代理架构、工具调用、规划和多智能体协作",
        "weight": 1.4
      }
    ],
    "blocked_topics": ["其他", "杂项"],
    "allow_new_topics": true
  }
}
```

实现规则：

- 词表保存在统一外部配置 JSON 中，设置页提供增删改和导入/导出；
- `preferred_topics` 是分类提示的一部分，但不能覆盖内容安全和输出结构要求；
- aliases 在保存前规范化，用于合并大小写、语言和近义写法；
- 用户主题优先复用，不强制每个提示命中主题；
- 权重只影响候选优先级，最终每条提示的主题权重仍归一化为 1；
- 修改词表后只标记受影响的分类版本失效，不应默认重跑全部历史；
- 分类结果记录词表版本，保证结果可追溯；
- 用户词表不能直接拼接为不受控系统指令，需要结构化转义并限制长度。

## 12. 对话卡片主题展示

“对话”模块的每个对话区块底部应显示该对话最有代表性的主题：

- 默认显示权重最高的 2 至 3 个主题标签；
- 标签显示在 Token 和提示数指标下方，超出部分显示 `+N`；
- 没有分类结果时显示“未分类”，并允许从卡片触发增量分类；
- 点击主题标签可直接筛选相同主题的对话；
- 合并多个提示的主题时使用加权总和，并避免一个提示的多个子主题重复放大；
- API 的对话列表响应应直接带轻量主题摘要，避免每张卡片再发一个请求；
- 主题标签必须包含账号和平台筛选上下文，不能把过滤范围外的数据算入。

## 13. 按平台切换视觉主题

接入 Gemini 后，界面配色由当前数据范围决定：

| 当前范围 | 视觉主题 | 建议设计令牌 |
|---|---|---|
| 仅 ChatGPT | 黑、白、灰，保持当前风格 | `theme-chatgpt` |
| 仅 Gemini | 淡蓝、白色，少量深蓝文字 | `theme-gemini` |
| 所有平台 | 绿色、白色，中性深色文字 | `theme-all` |

实现要求：

- 使用 CSS 自定义属性定义背景、面板、边框、强调色、热力格阶梯色和焦点环；
- 切换平台筛选时只切换根节点主题类，不复制整套组件样式；
- 色彩变化不能成为区分平台的唯一方式，标题、图标和账号标签也要明确显示平台；
- 三套主题都满足可读性、键盘焦点和常见色觉缺陷下的对比要求；
- 图表、主题标签、活动热力图、抽屉和空状态同时响应主题；
- 用户选择“所有平台”时采用绿—白主题，即使当前结果暂时只包含一个平台，也以筛选条件而不是结果数量决定配色；
- 将来增加其他 provider 时必须提供独立主题令牌或回退到中性主题。

### 合并视图必须合并统计数据

“所有平台”是数据聚合范围，不只是视觉主题。选择该范围时，后端必须在同一个查询中合并 ChatGPT、Gemini 以及未来其他 provider 的数据：

- 对话数按照 `(provider, account_id, remote_id)` 计数；
- 提示数以及输入、输出、总可见 Token 直接求和；
- 活跃天数按照用户配置时区转换后取日期并集，不能把同一天按平台重复计数；
- 日、周、月时间序列对同一周期求和；
- 每个周期的活跃对话数按照完整复合键去重；
- 新增对话数按照各平台对话的首次创建时间合并；
- 排行榜在全部平台数据上统一排序，并明确显示平台与账号；
- 主题统计先通过规范主题 ID、用户别名和合并规则归一化，再合计 prompt share 与 token share；
- 活动热力图、纪录、对话列表和主题时间线必须使用同一个 provider/account 筛选上下文；
- API 缓存键必须包含 provider、account、时区、日期范围和主题词表版本。

默认不按文本内容跨平台去重。用户可能把同一个问题分别发送给 ChatGPT 和 Gemini，这应视为两个真实活动记录。只有未来提供显式“跨平台重复内容分析”功能时，才能额外计算内容相似度，且不得改变基础统计。

界面至少提供：`全部平台`、`ChatGPT`、`Gemini` 三个范围，并可继续按账号细分。绿—白主题只在用户选择“全部平台”时启用；其统计卡、图表和列表必须同时显示合并后的结果。

## 14. 许可证与供应链方案

参考仓库采用 AGPL-3.0。VisualGPT 发布仓库采用 GPL-3.0，原始 `scrapemychats` MIT 许可文本保留在 `LICENSES/`；Gemini 连接器仍以可选外部依赖方式安装：

### 方案 A：可选外部依赖

VisualGPT 不复制参考项目源码，仅通过公开 Python 包接口调用用户自行安装的 `gemini-webapi`。在文档中明确其独立许可证和安装方式。仍需进一步确认分发与网络服务场景中的许可证义务。

### 方案 B：独立本地桥接进程

将 Gemini 连接器作为独立可选进程或插件，与主程序通过本地 JSON 协议通信，依赖、进程和许可证边界更清晰。

### 方案 C：独立实现

只参考公开行为和协议事实，不复制代码，重新实现最小的只读同步适配器。该方案仍面对非公开接口的稳定性与服务条款风险。

在法律和许可证评估完成前，不应把参考仓库源码直接合并进本仓库。

## 15. 分阶段实施

### 阶段 0：验证性原型

- 使用测试账号验证登录、列表范围和历史读取；
- 保存脱敏后的响应结构样本；
- 确认更新时间、分页和候选回复语义；
- 不写入正式数据库。

### 阶段 1：只读导入

- 增加 `provider` 数据库迁移；
- 实现 Gemini 原始快照与规范化；
- 不下载媒体，不启用提前停止；
- 为映射和幂等导入增加固定样本测试。

### 阶段 2：增量与多账号

- 每账号 checkpoint；
- 保守增量策略；
- 串行账号队列；
- UI 增加平台、账号筛选和同步状态。
- 完成平台主题令牌以及 ChatGPT、Gemini、全部平台三种配色。
- 对话卡片返回并显示主题摘要。

### 阶段 3：媒体与高级能力

- 可选媒体清单与下载；
- 候选回复展示；
- Gemini/ChatGPT 跨平台统计对比；
- 数据导出和可迁移备份。
- 活动页完整滑动窗口与大数据量虚拟化。
- 个性化主题词、别名、权重和分类版本管理。

## 16. 验收标准

- 同一远端 ID 在不同平台、账号下不会冲突；
- 重复同步不会产生重复会话或消息；
- 用户可见文本与隐藏 thoughts 分离；
- Cookie 不进入数据库、日志、API 响应或 Git；
- 单账号失败后队列可继续处理其他账号；
- 对话计数、提示数和 Token 统计可以按平台、账号及合并视图核对；
- 原始快照可以离线重建规范化数据；
- 对 Gemini 历史不完整的情况有明确 UI 提示，而不是宣称“完整备份”。
- 活动页可以从最早日期连续滑动到最新日期，所有月份均可定位且标签完整。
- 自定义主题词可以稳定复用，修改后只重算受影响的分类版本。
- 每个已分类的对话卡片显示主题标签，并能由标签发起筛选。
- ChatGPT、Gemini、全部平台分别呈现黑白、淡蓝白、绿白主题，且均满足可访问性要求。
- “全部平台”视图的各项总数等于相同筛选范围内各平台数据的正确聚合，活跃天数和活跃对话按复合键去重。

## 17. 参考资料

- [Gemini-API 项目说明](https://github.com/HanaokaYuzu/Gemini-API)
- [Gemini-API 客户端实现](https://github.com/HanaokaYuzu/Gemini-API/blob/master/src/gemini_webapi/client.py)
- [Gemini-API Python 与依赖声明](https://github.com/HanaokaYuzu/Gemini-API/blob/master/pyproject.toml)
- [Gemini-API AGPL-3.0 许可证](https://github.com/HanaokaYuzu/Gemini-API/blob/master/LICENSE)
