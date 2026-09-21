# Third-party references

Checked 2026-09-21. GPT Activity was implemented independently; no third-party source code was copied.

| Project | Observed license | Ideas reviewed |
|---|---|---|
| `luosx0403/chatgpt-sqlite-webui` | MIT | normalized SQLite archive, idempotent imports, local browsing |
| `meetpateltech/convelyze` | no repository license detected | activity calendar and dashboard information architecture only |
| `madpin/chatgpt-cost-dashboard` | MIT | SQLite-backed period aggregation |
| `carsonruebel/chatgpt-usage-analyzer` | MIT (README declaration) | privacy-first daily aggregation |
| `dagmawibabi/token-counter` | GPL-3.0 | conceptual token split only; no code copied |
| `Krishiv-Thakuria/ChatGPT-Wrapped` | no repository license detected | compact semantic sampling/privacy pattern only |
| `HanaokaYuzu/Gemini-API` | AGPL-3.0 | Gemini Web authentication, recent-chat listing, history retrieval and provider-adapter planning only |

The implementation uses its own parser, SQL schema, token aggregation, heatmap, and topic classifier. Projects with missing or incompatible licenses were treated only as product references.
