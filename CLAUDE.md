# CLAUDE.md — tech-blog-watch

每日追蹤大廠技術 blog、繁中摘要、發 Slack + Email 的 agent。使用者說明在 [README.md](README.md)。這份只記「Claude 進來工作要知道的規矩」；只適用於本機環境的個人備註在 `CLAUDE.local.md`（gitignored）。

## 架構定位

- **跑在 GitHub Actions**（雲端，不依賴 Tim 的 Mac）。這是**獨立 GitHub repo**，跟 `notes/` 各過各的。**主觸發是 cron-job.org 每天台北 06:15 打 workflow_dispatch**（GitHub schedule 2026 年平台級積壓、延遲數小時是常態且換分鐘無解）；repo 裡的 cron 只是 fallback。設定步驟在 README「排程與觸發」。
- **狀態存在 repo 裡**：`state.json` 記已看過的文章 URL、昨日脈動列點（`last_pulse`，48 小時窗口去重）、介紹過的 GitHub repo（`github_repos`）與看過沒選上的候選（`github_shown`）、介紹過的影片（`youtube_seen`）、當日已跑標記（`last_run_date`，`--once-per-day` guard 用），每天由 Actions commit 回去。這是雲端無狀態環境能「記得」的關鍵，別把它 gitignore 掉。
- 摘要用 Gemini API（`gemini-3.5-flash`，Google AI Studio 免費 tier；模型在 `sources.yaml` 可換）。用 `google-genai` SDK（`from google import genai`），不需要 Claude Code 在雲端跑。

## 單一事實來源

- **摘要風格/規則 → 只改 [`prompts/blog-digest.md`](prompts/blog-digest.md)**，不要寫死進 summarize.py。summarize.py 只負責讀 prompt、呼叫 API、解析結構化輸出。
- **脈動段風格/範圍 → 只改 [`prompts/industry-pulse.md`](prompts/industry-pulse.md)**（含金融×AI 窄門的鬆緊），不要寫死進 pulse.py。pulse.py 只負責帶 `google_search` 工具呼叫 Gemini、抽 grounding 來源；段落定義（開關名/標題/prompt 檔）在 `pulse.SECTIONS`。（獨立的金融×AI 段 2026-08-31 已停用併入產業段——44 天實測 12% 命中率、天天灌水；`prompts/finance-ai-pulse.md` 留檔備查，若要復活建議走週頻。）
- **GitHub 週段選題口味 → 只改 [`prompts/github-weekly.md`](prompts/github-weekly.md)**。github_watch.py 只負責蒐集候選（HN Algolia API + GitHub Trending 頁）、去重/更新判斷、呼叫 Gemini（一般結構化輸出，非 grounded）。
- **YouTube 週段選題口味 → 只改 [`prompts/youtube-weekly.md`](prompts/youtube-weekly.md)**；頻道白名單只改 [`sources.yaml`](sources.yaml) 的 `youtube_channels`。youtube_watch.py 只負責抓頻道 feed、去重、呼叫 Gemini（一般結構化輸出，非 grounded）。
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
- **排程與 guard**：fallback cron 是 UTC（`17 22 * * *` = 台北 06:17；避開整點/半點）。主觸發 cron-job.org 打 dispatch，兩者同日重複靠 main.py 的 `--once-per-day`（比對 `state.json` 的 `last_run_date`，台北日期）擋。**workflow 的 checkout 必須留 `ref: main`**：schedule run 的 GITHUB_SHA 釘在 run 建立當下的 commit，排隊排到前一個 run push 完 state 之後才跑的話，不加 ref 會讀到舊 state、guard 失效重複發信。`skip_weekdays: [6]`＝台北週日整天不發（gate 在 main.py，不動 cron——延遲跨日會讓 cron 層的星期判斷不可靠），當天不抓不標記、內容滾入週一。
- **改 workflow 或 secrets 後**，下一次排程或手動 `workflow_dispatch` 才生效。手動 dispatch 勾 `force` 可跳過 `--once-per-day`（同日重跑測試用）。
- **GitHub 週段**：只在特定**台北時間**星期跑（`github_weekly_weekdays`，list，0=週一；目前 `[0, 2, 4]`＝一/三/五；也相容舊的單數 `github_weekly_weekday`。gating 用 `ZoneInfo("Asia/Taipei")`，別用 runner 的 UTC）。`top_n` 目前 4：2026-08 供給實測（HN 合格 ~15.7 個/週）顯示三天排程維持 5 會稀釋品質。介紹過的 repo 記在 `github_repos`；重覆出現的條件是「隔 `github_refeature_days` 天以上**且**之後有新 GitHub Release」。**送模型看過但沒選上的候選記在 `github_shown`**（`github_shown_cooldown_days` 內不重送；只在模型有回應時記錄，API 失敗不記）。GitHub API（README/release）走 `GITHUB_TOKEN`（Actions 自動提供；本機沒 token 也能跑、只是限流 60 次/hr）。測試用 `--force-github`（非 grounded、不吃 grounded 額度）。
- **YouTube 週段**：只在 `youtube_weekly_weekdays`（目前 `[3]`＝週四，台北）跑。頻道 feed 用 **UULF playlist feed**（`channel_id` 的 `UC` 前綴換 `UULF`）排除 Shorts、失敗退回 channel feed；feed 免 API key 但**沒有時長欄位**，宣傳短片靠選題 prompt 從說明厚薄判斷。`youtube_channels` 的 `channel_id` 一定要 `UC` 開頭的 canonical id（`@handle` 解析不到）。介紹過的影片記在 `youtube_seen`。目前是 metadata-only 導讀（不看影片）；若升級影片理解：免費 tier 每天上限 8 小時 YouTube 影片、60 分鐘片要 `mediaResolution: LOW`（≈360k input tokens，先確認該模型免費 TPM 撐不撐得住）。**不要**用 youtube-transcript-api / yt-dlp 抓字幕——YouTube 按 ASN 封鎖雲端 IP，Actions 上必死（2026-08 查證）。測試用 `--force-youtube`。
- **脈動段（pulse）**：目前一段「AI 產業脈動」（含金融×AI 窄門），每天花 1 次 grounded query（dry-run 也一樣）。Gemini Grounding with Google Search **不可**與 `response_schema` 併用（citations 會空），所以走純文字輸出、prompt 要求每行一點、程式再切列點。**免費 tier 的 grounding 只有 2.5 系列有**（`gemini-2.5-flash` 免費 500 RPD、官方無退役日期）；**3.x 全系列在免費 key 上 grounding 一律 429**（pricing 頁 free tier 標 Not available，付費 tier 才有每月 5,000 次內含額度）——所以 `pulse_model` 鎖 `gemini-2.5-flash`，若它公告退役，替代路是開帳單或改非 grounded。模型偶爾不搜尋就作答 → pulse.py 會加強提示重試一次，仍無佐證就標 `grounded: false`。窗口是 48 小時（吃排程延遲的保險），靠 `last_pulse` 餵昨日列點去重。任一段失敗不會擋文章 digest、也不會擋其他段。

## 跑法

```bash
.venv/bin/python main.py --dry-run   # 抓+摘要+印，不發不寫
.venv/bin/python main.py --seed      # 只標記已看過
.venv/bin/python main.py             # 正式：發送 + 更新 state
# --force-github / --force-youtube：不管星期幾都跑該週段（搭配 --dry-run 測試）
# --once-per-day：workflow 專用（同日已跑過就退出），本機不用帶
```
