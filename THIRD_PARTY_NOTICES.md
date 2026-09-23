# 第三方归属说明

## scrapemychats

VisualGPT 的 ChatGPT 浏览器登录、会话获取和旧版静态导出能力源自 `scrapemychats` 项目，并在其基础上扩展了 SQLite 分析、多账号同步、React 仪表盘、主题分类和官方导出导入。

原始项目采用 MIT 许可证。许可文本保存在 [`LICENSES/scrapemychats-MIT.txt`](LICENSES/scrapemychats-MIT.txt)。

## Gemini-API

[`HanaokaYuzu/Gemini-API`](https://github.com/HanaokaYuzu/Gemini-API) 作为可选外部 Python 依赖提供 Gemini Web 连接能力。本仓库没有复制或合并该项目的源代码。该项目采用 AGPL-3.0；启用 Gemini 功能的用户和再分发者需要同时遵守其许可证。

## EasyInternship

WeChat 4.x 的只读数据库、消息解析与群聊 sender 推断逻辑移植自同一作者的 [`Sundance0604/EasyInternship`](https://github.com/Sundance0604/EasyInternship)。移植部分保留其 GPL-3.0 许可语义，并针对 Aistory 的多账号身份、统一 conversation/message schema 与 SQLite checkpoint 做了适配。

其他产品与开源项目调研记录见 [`docs/third_party_references.md`](docs/third_party_references.md)。
