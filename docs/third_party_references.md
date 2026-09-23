# Third-party references

Checked 2026-09-23. The original GPT Activity implementation was independent; the WeChat provider now explicitly ports verified reader logic from the same author's GPL-3.0 EasyInternship repository.

| Project | Observed license | Ideas reviewed |
|---|---|---|
| `luosx0403/chatgpt-sqlite-webui` | MIT | normalized SQLite archive, idempotent imports, local browsing |
| `meetpateltech/convelyze` | no repository license detected | activity calendar and dashboard information architecture only |
| `madpin/chatgpt-cost-dashboard` | MIT | SQLite-backed period aggregation |
| `carsonruebel/chatgpt-usage-analyzer` | MIT (README declaration) | privacy-first daily aggregation |
| `dagmawibabi/token-counter` | GPL-3.0 | conceptual token split only; no code copied |
| `HanaokaYuzu/Gemini-API` | AGPL-3.0 | optional external dependency for Gemini Web RPC access; no source copied |
| `Sundance0604/EasyInternship` | GPL-3.0 | WeChat 4.x read-only SQL, payload normalization, shard cursor, and group sender inference ported into Aistory |
| `Krishiv-Thakuria/ChatGPT-Wrapped` | no repository license detected | compact semantic sampling/privacy pattern only |

The implementation uses its own parser, SQL schema, token aggregation, heatmap, and topic classifier. Projects with missing or incompatible licenses were treated only as product references.
