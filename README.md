# VisualGPT / GPT Activity

一个本地优先的 ChatGPT 对话同步、归档与活动分析工具。它把对话保存到本机 SQLite，提供中文 Web 仪表盘，并支持多账号串行同步、官方导出文件导入、可见 Token 估算和可选的主题分类。

> 本项目使用 ChatGPT Web 的非公开接口。上游接口可能随时变化；请控制请求频率，仅同步你有权访问的账号和数据。

## 功能

- 增量同步 ChatGPT 对话，正常模式遇到首个未变化对话后停止继续翻页。
- 多账号独立登录、独立原始数据目录，按照配置顺序串行同步。
- 导入 ChatGPT 官方导出 ZIP、`conversations.json`、单个 `conversation.json` 或对话目录。
- 正确选择当前活动分支，排除放弃的编辑/重新生成分支。
- 统计提示数、对话数、活跃天数以及输入、输出、总可见 Token。
- 日、周、月活动趋势与可点击的年度热力图。
- 对话搜索、日期和账号筛选、消息与对话排行榜。
- 使用 DeepSeek 等 OpenAI 兼容接口进行可选的层级主题分类。
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
        └── work/
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
git clone git@github.com:Sundance0604/VisualGPT.git
cd VisualGPT

python -m pip install -r requirements.txt
python -m playwright install chromium

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
  "accounts": [
    {
      "id": "default",
      "name": "个人账号",
      "browser_profile": "browser_profile",
      "enabled": true
    }
  ],
  "topics": {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key": "",
    "max_concurrency": 4
  }
}
```

说明：

- 相对路径以 `config.local.json` 所在目录为基准。
- `browser_channel` 可设置为 `chrome` 或 `msedge`。
- `include_files` 默认为 `false`，不会扫描账号的 File Library。
- `stop_on_first_unchanged` 开启时，普通同步会在更新时间倒序列表中遇到首个未变化对话后停止翻页。
- `--full-index` 会忽略提前停止规则，执行完整索引核对。
- 修改数据库路径会创建或打开目标数据库，不会自动搬迁旧数据库。
- 主题 API 密钥也可通过环境变量 `GPT_ACTIVITY_API_KEY` 临时提供。
- 可通过 `GPT_ACTIVITY_CONFIG` 指定另一份配置文件。

## 启动仪表盘

```powershell
python -m gpt_activity serve
```

浏览器访问：<http://127.0.0.1:8765>

仪表盘包括概览、活动、纪录、主题、对话、同步和设置页面。点击活动热力格可查看当天提示数及输入、输出、总可见 Token。

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
python -m gpt_activity accounts list
```

每个账号必须使用不同的 `browser_profile`。运行无参数的 `sync` 时，程序会按照 `accounts` 数组中的顺序逐个同步账号，不会并发。

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

## 本地接口

主要接口：

- `GET /api/summary`：总览统计
- `GET /api/activity/{daily|weekly|monthly}`：活动时间序列
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

## Gemini 扩展计划

未来接入 Gemini 的架构、认证方式、数据映射、许可证风险与分阶段实施方案见 [docs/gemini_expansion_plan.md](docs/gemini_expansion_plan.md)。

路线图还包括：活动页完整滑动时间窗口、个性化主题词、对话卡片主题标签，以及按 ChatGPT、Gemini、全部平台分别切换黑白、淡蓝白、绿白视觉主题。“全部平台”会实际合并对话、提示、Token、活跃天数、时间序列、主题和排行榜，不只是更换配色。

## 旧版导出器

原来的导出流程仍然保留：

```powershell
python export_chats.py --limit 3
python export_chats.py
python build_viewer.py
```

旧版脚本也不会默认扫描 File Library；只有显式添加 `--include-library` 才会启用。

## 许可证

VisualGPT 仓库采用 GPL-3.0 许可证，参见 [LICENSE](LICENSE)。项目包含源自 MIT 许可 `scrapemychats` 的部分，原始许可文本保存在 [LICENSES/scrapemychats-MIT.txt](LICENSES/scrapemychats-MIT.txt)。第三方归属与调研信息见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [docs/third_party_references.md](docs/third_party_references.md)。
