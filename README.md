# Aistory

**History is Aistory**

一个本地优先的 ChatGPT / Gemini 对话同步、归档与活动分析工具。它把对话保存到本机 SQLite，提供中文 Web 仪表盘，并支持多平台多账号串行同步、ChatGPT 官方导出导入、可见 Token 估算和可选的主题分类。

> 本项目使用 ChatGPT Web 的非公开接口。上游接口可能随时变化；请控制请求频率，仅同步你有权访问的账号和数据。

## 功能

- 增量同步 ChatGPT 对话，正常模式遇到首个未变化对话后停止继续翻页。
- 多账号独立登录、独立原始数据目录，按照配置顺序串行同步。
- Gemini 普通与置顶会话完整游标分页、保守增量读取和逐会话失败恢复。
- 导入 ChatGPT 官方导出 ZIP、`conversations.json`、单个 `conversation.json` 或对话目录。
- 正确选择当前活动分支，排除放弃的编辑/重新生成分支。
- 统计提示数、对话数、活跃天数以及输入、输出、总可见 Token。
- 日、周、月完整可滚动趋势、可点击的年度热力图和按日对话下钻。
- 物化日统计、对话生命周期分析，以及 ChatGPT / Gemini / 全部平台合并筛选。
- 全局使用时长推断：交互 gap 分布、Token 描述性关联、gap-only GMM/HMM、显式边界、异常诊断和模型分歧。
- 对话搜索、日期和账号筛选、消息与对话排行榜。
- 使用 DeepSeek 等 OpenAI 兼容接口进行可选的层级主题分类。
- 个性化主题词、稳定主题颜色和对话卡片主题摘要。
- 原始 JSON 原子保存，SQLite 作为统一分析数据源。

## 数据如何保存

所有账号共享一个 SQLite 数据库，但每条记录都有账号标识。不同账号即使出现相同的远端对话 ID，也不会相互覆盖。

```text
data/
├── gpt_activity.db
└── raw/
    └── conversations/
        ├── default/
        │   └── <conversation-id>.json
        ├── work/
            └── <conversation-id>.json
        └── gemini-personal/
            └── <conversation-id>.json
```

数据库、原始对话、浏览器登录资料和 `config.local.json` 均被 Git 忽略，不会随代码上传。

## 环境要求

- Python 3.11 或更高版本
- Node.js 20 或更高版本
- Google Chrome 或 Microsoft Edge
- Windows、macOS 或 Linux

如果已经有 Conda 环境，可以直接使用：

```powershell
conda activate pavane
```

也可以创建普通虚拟环境：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

## 安装

```powershell
git clone git@github.com:Sundance0604/Aistory.git
cd Aistory

python -m pip install -r requirements.txt
python -m playwright install chromium

# 需要 Gemini 同步时再安装可选依赖
python -m pip install ".[gemini]"

cd frontend
npm install
npm run build
cd ..
```

复制配置模板：

```powershell
Copy-Item config.example.json config.local.json
```

Linux/macOS 使用：

```bash
cp config.example.json config.local.json
```

## 配置

所有外部配置集中在 `config.local.json`。重要配置如下：

```json
{
  "app": {
    "host": "127.0.0.1",
    "port": 8765,
    "timezone": "Asia/Shanghai"
  },
  "storage": {
    "method": "filesystem",
    "database_path": "data/gpt_activity.db",
    "raw_conversations_dir": "data/raw/conversations",
    "import_roots": []
  },
  "chatgpt": {
    "base_url": "https://chatgpt.com",
    "browser_channel": "chrome",
    "include_files": false,
    "min_delay_seconds": 10,
    "max_delay_seconds": 16,
    "stop_on_first_unchanged": true
  },
  "gemini": {
    "enabled": false,
    "secure_1psid": "",
    "secure_1psidts": "",
    "proxy": "",
    "page_size": 100,
    "read_limit": 10000,
    "recent_refetch_count": 30,
    "retry_delays_seconds": [1, 3, 10]
  },
  "analytics": {
    "session_gap_minutes": 30,
    "single_prompt_minutes": 5,
    "session_tail_minutes": 5
  },
  "accounts": [
    {
      "id": "default",
      "name": "个人账号",
      "provider": "chatgpt",
      "browser_profile": "browser_profile",
      "enabled": true
    }
  ],
  "topics": {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key": "",
    "max_concurrency": 4,
    "preferences": {
      "keywords": ["机器学习", "写作"],
      "aliases": {},
      "weights": {},
      "blocked_topics": []
    }
  }
}
```

说明：

- 相对路径以 `config.local.json` 所在目录为基准。
- `browser_channel` 可设置为 `chrome` 或 `msedge`。
- `include_files` 默认为 `false`，不会扫描账号的 File Library。
- `stop_on_first_unchanged` 开启时，普通同步会在更新时间倒序列表中遇到首个未变化对话后停止翻页。
- `--full-index` 会忽略提前停止规则，执行完整索引核对。
- Gemini Cookie 可保存在这份本地 JSON 中；环境变量 `GEMINI_1PSID`、`GEMINI_1PSIDTS` 仅作为可选覆盖。
- 设置 API 会清空 Cookie 和 API 密钥字段后再返回，`config.local.json` 不得提交。
- 生命周期默认以 30 分钟提示间隔划分会话段；修改时区或口径后会重建派生统计。
- 修改数据库路径会创建或打开目标数据库，不会自动搬迁旧数据库。
- 主题 API 密钥也可通过环境变量 `GPT_ACTIVITY_API_KEY` 临时提供。
- 可通过 `GPT_ACTIVITY_CONFIG` 指定另一份配置文件。

## 启动仪表盘

```powershell
python -m gpt_activity serve
```

浏览器访问：<http://127.0.0.1:8765>

仪表盘包括概览、活动、时长、生命周期、纪录、主题、对话、同步和设置页面。点击活动柱、日期行或热力图日期块可查看当天提示数、Token、平台、账号、主主题及对话明细。平台选择器会同时改变查询范围和页面主题；“全部”是真实合并统计，不只是换色。

## 首次同步

```powershell
python -m gpt_activity sync --account default
```

程序会打开真实浏览器。首次运行时登录 ChatGPT；之后会复用对应的浏览器资料目录。

常用命令：

```powershell
# 增量同步全部已启用账号
python -m gpt_activity sync

# 完整扫描远端轻量索引
python -m gpt_activity sync --full-index

# 强制重新获取可见对话
python -m gpt_activity sync --force-fetch

# 明确选择账号，仍然按顺序串行执行
python -m gpt_activity sync --account personal --account work
```

同步过程中不要同时启动第二个同步任务，也不要让两个进程打开同一个浏览器资料目录。

## 添加多个账号

可以在仪表盘“设置 → 账号与浏览器资料”中添加，也可以使用 CLI：

```powershell
python -m gpt_activity accounts add personal "个人账号" --profile browser_profiles/personal
python -m gpt_activity accounts add work "工作账号" --profile browser_profiles/work
python -m gpt_activity accounts add gemini-personal "Gemini 个人账号" --provider gemini
python -m gpt_activity accounts list
```

每个账号必须使用不同的 `browser_profile`。运行无参数的 `sync` 时，程序会按照 `accounts` 数组中的顺序逐个同步账号，不会并发。

## 配置与同步 Gemini

在“设置 → Gemini 读取”或 `config.local.json` 中填写 `Secure-1PSID` 与 `Secure-1PSIDTS`，再添加 `provider: "gemini"` 的账号。Cookie 会持久保存在本地配置中，不依赖每次启动 Conda 后重新设置环境变量。

```powershell
python -m gpt_activity sync --account gemini-personal
```

Gemini 同步会完整分页读取普通与置顶会话并按 ID 去重。用户的纯图片等非文本回合会保留为零 Token、不可分类的附件事件；不会下载或 OCR 媒体。Gemini Web 同样是非公开接口，Cookie 失效后需要重新填写。更多实现口径见 [docs/gemini_analytics_implementation.md](docs/gemini_analytics_implementation.md)。

如果同步后仍显示 0 条，请先查看“同步”页的账号级错误：连接超时通常表示需要在“设置 → Gemini 读取”填写本机 HTTP 代理（例如 `http://127.0.0.1:7890`）；“unauthenticated / permission denied”表示 Cookie 已失效，需要从已登录的 Gemini 网页重新复制 `Secure-1PSID` 与 `Secure-1PSIDTS`。应用不会再把未认证响应误报为“成功同步 0 条”。

## 导入 ChatGPT 官方导出数据

支持以下输入：

- ChatGPT 官方导出 ZIP；
- 官方导出中的 `conversations.json`；
- 单个对话的 `conversation.json`；
- 包含多个 `conversation.json` 的目录。

```powershell
python -m gpt_activity import-json D:\Downloads\chatgpt-export.zip `
  --account personal `
  --account-name "个人账号"
```

导入具有幂等性：再次导入内容相同的对话不会产生重复记录。也可以在仪表盘“同步”页面输入文件或目录路径进行导入。

## 使用已有对话目录

```powershell
python -m gpt_activity import-json complete_export --account default
```

如果不需要保留原始 JSON 副本：

```powershell
python -m gpt_activity import-json complete_export --no-raw-copy
```

## 主题分析

主题分析是可选功能。未配置模型 API 时，Token、日期、热力图、排行榜和对话浏览仍然正常工作。

在 `config.local.json` 中填写模型地址、模型名称和 API 密钥，然后运行：

```powershell
# 只分类尚未处理的用户提示
python -m gpt_activity topics classify-new

# 明确重新分类全部提示
python -m gpt_activity topics reclassify
```

发送给主题模型的内容仅包括对话标题、当前用户提示和最多两个先前用户提示，不会发送完整对话或附件。API 密钥不会写入 SQLite、日志或设置接口响应。

## 指标定义

- **提示**：活动分支上的一次用户回合；只有附件而没有文本的用户回合仍计为一个提示。
- **输入可见 Token**：用户可见文本的估算 Token。
- **输出可见 Token**：助手可见文本的估算 Token。
- **总可见 Token**：输入与输出可见 Token 之和。

这些数字不是服务商账单数据。系统指令、工具调用、隐藏推理、记忆、缓存上下文以及被放弃的编辑/重新生成分支不会计入。

### 全局使用时长

“时长”页面把所有平台、账号和对话中的用户事件合并成同一条时间线，不把 conversation 或平台切换视为 Session 边界。核心 GMM 与 HMM 都只使用 `log1p(gap_seconds)`；Token 只保留作相关性和回归等描述性分析。页面展示 K=1–6 的 gap-only GMM 候选、Short-gap / Long-gap 间隔状态、后验边界概率、显式边界时间线、模型混淆矩阵，以及 Session 时长和边界异常诊断。

GMM 与 HMM 是两种并列的模型设定，不显示唯一“真值”。HMM 状态表示相邻交互的间隔尺度，而不是用户的活跃/离开状态。边界概率阈值默认 0.5，Session 尾部余量默认 5 分钟，可在设置或 `config.local.json` 的 `usage_time.boundary_threshold`、`usage_time.tail_allowance_minutes` 修改。最终结果标为“推断的 Session 使用时长”，模型范围表示设定敏感性，不是统计置信区间。模型会在同步、导入或相关设置变化后重建并写入 SQLite。

## 本地接口

主要接口：

- `GET /api/summary`：总览统计
- `GET /api/activity/{daily|weekly|monthly}`：活动时间序列
- `GET /api/activity/{date}/conversations`：某日对话明细
- `GET /api/lifecycle`：对话生命周期统计
- `GET /api/usage-time/summary`：全局时长模型摘要与双模型估算
- `GET /api/usage-time/distribution`：原始 interaction gap 分布
- `GET /api/usage-time/associations`：Token-gap 关联与描述性回归
- `GET /api/usage-time/model-candidates`：GMM/HMM 候选、AIC 与 BIC
- `GET /api/usage-time/boundaries`：最近的 gap、边界概率与两模型决策
- `GET /api/usage-time/{gmm|hmm|disagreements|sessions}`：模型解释、分歧和 sessions
- `GET /api/conversations`：对话列表与筛选
- `POST /api/sync`：启动同步
- `GET /api/sync/status`：同步状态
- `GET/POST /api/accounts`：账号配置
- `POST /api/import`：导入本地导出数据
- `GET /api/storage`：当前保存方式和路径
- `GET /api/data-sources`：导入来源记录
- `GET /api/topics`：主题分布

原始数据保存通过 `RawConversationStorage` 接口实现。当前提供 `filesystem` 后端，以临时文件加原子替换的方式保存 JSON。

## 隐私与安全

- 对话、SQLite、原始 JSON 和浏览器登录资料默认只保存在本机。
- 项目不包含遥测。
- File Library 扫描默认关闭。
- 主题分析是唯一会把提示文本发送给外部模型 API 的功能。
- 不要提交或分享 `config.local.json`、`browser_profile/`、`browser_profiles/`、`data/` 或任何导出目录。
- 浏览器资料目录包含登录状态，应视为敏感凭据。
- Gemini Cookie 与 API 密钥保存在 `config.local.json` 时同样属于敏感凭据；设置 API 只返回是否已配置。
- ChatGPT Web 接口为非公开接口，使用前请自行评估账号和服务条款风险。

## 开发

```powershell
# 后端测试
pytest -p no:cacheprovider

# 前端开发
cd frontend
npm run dev

# 类型检查与生产构建
npm run typecheck
npm run build
```

后端使用 FastAPI 与 SQLite，前端使用 React、TypeScript 和 Vite。生产构建后的静态文件由 FastAPI 提供。

## Gemini 实现与后续扩展

当前 Gemini 和物化统计的实现口径见 [docs/gemini_analytics_implementation.md](docs/gemini_analytics_implementation.md)。早期架构路线与未来媒体能力见 [docs/gemini_expansion_plan.md](docs/gemini_expansion_plan.md)。

活动页完整滑动时间窗口、个性化主题词、对话卡片主题标签，以及 ChatGPT 黑白、Gemini 淡蓝白、全部平台绿白视觉主题均已接入。“全部平台”会实际合并对话、提示、Token、活跃天数、时间序列、主题和排行榜。

## 旧版导出器

原来的导出流程仍然保留：

```powershell
python export_chats.py --limit 3
python export_chats.py
python build_viewer.py
```

旧版脚本也不会默认扫描 File Library；只有显式添加 `--include-library` 才会启用。

## 许可证

VisualGPT 发布仓库采用 GPL-3.0 许可证，参见 [LICENSE](LICENSE)。原始 `scrapemychats` 代码的 MIT 许可文本保留在 [LICENSES/scrapemychats-MIT.txt](LICENSES/scrapemychats-MIT.txt)，第三方说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
