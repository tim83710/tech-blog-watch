# CLAUDE.md — tech-blog-watch

每日追蹤大廠技術 blog、繁中摘要、發 Slack + Email 的 agent（`mind/` 容器底下的獨立專案，容器總覽見 [../CLAUDE.md](../CLAUDE.md)）。使用者說明在 [README.md](README.md)。這份只記「Claude 進來工作要知道的規矩」。

## 架構定位

- **跑在 GitHub Actions**（雲端排程，不依賴 Tim 的 Mac）。這是**獨立 GitHub repo**，跟 `notes/` 各過各的。
- **狀態存在 repo 裡**：`state.json` 記已看過的文章 URL，每天由 Actions commit 回去。這是雲端無狀態環境能「記得」的關鍵，別把它 gitignore 掉。
- 摘要用 Gemini API（`gemini-3.5-flash`，Google AI Studio 免費 tier；模型在 `sources.yaml` 可換）。用 `google-genai` SDK（`from google import genai`），不需要 Claude Code 在雲端跑。

## 單一事實來源

- **摘要風格/規則 → 只改 [`prompts/blog-digest.md`](prompts/blog-digest.md)**，不要寫死進 summarize.py。summarize.py 只負責讀 prompt、呼叫 API、解析結構化輸出。
- **來源清單 → 只改 [`sources.yaml`](sources.yaml)**。

## 撰寫紀律（沿用 notes/ 的摘要紀律）

摘要是要分享給同事看的，語氣同 `notes/` 的 summary.md：

1. **繁中書寫、技術名詞保留英文**（Genie、Lakebase、NVLink、MCP…）
2. **不編造**：只根據原文；不確定的不寫
3. **引用原文一字不差**（放 `quotes`，英文原句 + 繁中說明）
4. **重點導向**：抓「發佈什麼、解決什麼、對誰有用、跟競品關係」，省略行銷語

## 操作守則

- **獨立 `.venv`**（Python 3.12），別碰 `notes/.venv`、更別碰 `~/pyWork/myenv`（見 auto-memory `feedback_isolate-deps`）
- **Slack 發送沿用 Tim 既有模式**：`slack_sdk.webhook.WebhookClient` + `send_dict`（參考 `~/pyWork/company_dashboard/common/utils.py` 的 `Slacker`）
- **Email 用私人 Gmail SMTP**：`SMTP_PASSWORD` 是「應用程式密碼」不是登入密碼
- **Secrets 不進 repo**：本機用 `.env`（已 gitignore），雲端用 GitHub Actions Secrets

## 容易踩雷

- **首次執行**：`state.json` 空時 `main.py` 自動走 seed 模式（只標記已看過、不摘要不發送），避免第一次把整個 backlog 灌成巨量 digest。要重置就把 `state.json` 清成 `{"seen": {}}`。
- **無 RSS 的來源**（Databricks、Anthropic、OpenAI Developers）走 scrape，靠 `sources.yaml` 的 `link_pattern` 從列表頁挑文章連結；對方改版時 pattern 可能要調。
- **cron 是 UTC**：`30 22 * * *` = 隔天台北 06:30。（GitHub 排程 best-effort，尖峰常延遲數小時，實際到信會晚於此。）
- **改 workflow 或 secrets 後**，下一次排程或手動 `workflow_dispatch` 才生效。

## 跑法

```bash
.venv/bin/python main.py --dry-run   # 抓+摘要+印，不發不寫
.venv/bin/python main.py --seed      # 只標記已看過
.venv/bin/python main.py             # 正式：發送 + 更新 state
```
