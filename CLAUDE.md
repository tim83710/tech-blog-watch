# CLAUDE.md — tech-blog-watch

每日追蹤大廠技術 blog、繁中摘要、發 Slack + Email 的 agent。使用者說明在 [README.md](README.md)。這份只記「Claude 進來工作要知道的規矩」；只適用於本機環境的個人備註在 `CLAUDE.local.md`（gitignored）。

## 架構定位

- **跑在 GitHub Actions**（雲端排程，不依賴 Tim 的 Mac）。這是**獨立 GitHub repo**，跟 `notes/` 各過各的。
- **狀態存在 repo 裡**：`state.json` 記已看過的文章 URL 和昨日脈動列點（`last_pulse`，48 小時窗口去重用），每天由 Actions commit 回去。這是雲端無狀態環境能「記得」的關鍵，別把它 gitignore 掉。
- 摘要用 Gemini API（`gemini-3.5-flash`，Google AI Studio 免費 tier；模型在 `sources.yaml` 可換）。用 `google-genai` SDK（`from google import genai`），不需要 Claude Code 在雲端跑。

## 單一事實來源

- **摘要風格/規則 → 只改 [`prompts/blog-digest.md`](prompts/blog-digest.md)**，不要寫死進 summarize.py。summarize.py 只負責讀 prompt、呼叫 API、解析結構化輸出。
- **脈動段風格/範圍 → 只改 prompt 檔**：AI 產業段改 [`prompts/industry-pulse.md`](prompts/industry-pulse.md)、金融×AI 段改 [`prompts/finance-ai-pulse.md`](prompts/finance-ai-pulse.md)，同理不要寫死進 pulse.py。pulse.py 只負責帶 `google_search` 工具呼叫 Gemini、抽 grounding 來源；段落定義（開關名/標題/prompt 檔）在 `pulse.SECTIONS`。
- **GitHub 週段選題口味 → 只改 [`prompts/github-weekly.md`](prompts/github-weekly.md)**。github_watch.py 只負責蒐集候選（HN Algolia API + GitHub Trending 頁）、去重/更新判斷、呼叫 Gemini（一般結構化輸出，非 grounded）。
- **來源清單 → 只改 [`sources.yaml`](sources.yaml)**。

## 撰寫紀律（沿用 notes/ 的摘要紀律）

摘要是要分享給同事看的，語氣同 `notes/` 的 summary.md：

1. **繁中書寫、技術名詞保留英文**（Genie、Lakebase、NVLink、MCP…）
2. **不編造**：只根據原文；不確定的不寫
3. **引用原文一字不差**（放 `quotes`，英文原句 + 繁中說明）
4. **重點導向**：抓「發佈什麼、解決什麼、對誰有用、跟競品關係」，省略行銷語

## 操作守則

- **獨立 `.venv`**（Python 3.12），相依不與其他專案共用
- **Slack 發送模式**：`slack_sdk.webhook.WebhookClient` + `send_dict`（Incoming Webhook，不用 bot token）
- **平台開關**：`SEND_SLACK` / `SEND_EMAIL` 環境變數（雲端設在 GitHub repo **Variables**，非 Secrets；本機走 `.env`）。設 `false` 停發該平台、沒設＝都發；判斷在 `main._channel_enabled`，seed 模式的初始化通知也吃同一開關
- **Email 用私人 Gmail SMTP**：`SMTP_PASSWORD` 是「應用程式密碼」不是登入密碼
- **Secrets 不進 repo**：本機用 `.env`（已 gitignore），雲端用 GitHub Actions Secrets

## 容易踩雷

- **首次執行**：`state.json` 空時 `main.py` 自動走 seed 模式（只標記已看過、不摘要不發送），避免第一次把整個 backlog 灌成巨量 digest。要重置就把 `state.json` 清成 `{"seen": {}}`。
- **無 RSS 的來源**（Databricks、Anthropic、OpenAI Developers）走 scrape，靠 `sources.yaml` 的 `link_pattern` 從列表頁挑文章連結；對方改版時 pattern 可能要調。
- **cron 是 UTC**：`30 22 * * *` = 隔天台北 06:30。（GitHub 排程 best-effort，尖峰常延遲數小時，實際到信會晚於此。）
- **改 workflow 或 secrets 後**，下一次排程或手動 `workflow_dispatch` 才生效。
- **GitHub 週段**：只在特定**台北時間**星期跑（`github_weekly_weekdays`，list，0=週一；目前 `[1, 4]`＝週二、週五；也相容舊的單數 `github_weekly_weekday`。gating 用 `ZoneInfo("Asia/Taipei")`，別用 runner 的 UTC）。介紹過的 repo 記在 `state.json` 的 `github_repos`；重覆出現的條件是「隔 `github_refeature_days` 天以上**且**之後有新 GitHub Release」，沒發正式 Release 的 repo 不會重覆。GitHub API（README/release）走 `GITHUB_TOKEN`（Actions 自動提供，workflow 已帶入；本機沒 token 也能跑、只是限流 60 次/hr）。測試用 `--force-github`（模型呼叫非 grounded、不吃 grounded 額度）。
- **脈動段（pulse）**：現有兩段——「AI 產業脈動」與「金融×AI 脈動」，各自獨立生成、每天各花 1 次 grounded query（dry-run 也一樣，目前 2 段=2 次）。Gemini Grounding with Google Search **不可**與 `response_schema` 併用（citations 會空），所以走純文字輸出、prompt 要求每行一點、程式再切列點。**grounded query 的免費額度依模型系列而異**：實測 `gemini-3.5-flash` 帶 `google_search` 在免費 key 上直接 429（要計費），所以 `pulse_model` 固定用 `gemini-2.5-flash`（每日免費額度）；一般摘要不受影響。模型偶爾不搜尋就作答 → pulse.py 會加強提示重試一次，仍無佐證就標 `grounded: false`。窗口是 48 小時（吃排程延遲的保險），靠 state.json 的 `last_pulse` 餵昨日列點給 prompt 去重。任一段失敗不會擋文章 digest、也不會擋另一段。

## 跑法

```bash
.venv/bin/python main.py --dry-run   # 抓+摘要+印，不發不寫
.venv/bin/python main.py --seed      # 只標記已看過
.venv/bin/python main.py             # 正式：發送 + 更新 state
```
