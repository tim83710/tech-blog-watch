# tech-blog-watch

每日自動追蹤各大科技廠商技術部落格，有新文章就用**繁體中文**摘要（附原文重點引用），發到 **Slack** 與 **Email**。跑在 **GitHub Actions** 上，不依賴本機開機。

每日 digest 開頭另附「**AI 產業脈動**」快訊列點，用 Gemini 的 Google Search grounding 即時搜尋、總結過去 48 小時的產業大事（投資視角：洗牌級發佈、晶片供應鏈、改變格局的融資併購，也含金融業 AI 落地的高門檻事件；昨日已報導過的自動排除）。列點呈現、來源連結附在後面；大廠 blog 沒新文章的日子也會單獨發這段。（曾有獨立的「金融×AI 脈動」段，2026-08 起併入產業段。）

每週固定加段（台北時間；星期幾可在 `sources.yaml` 調）：

- **一/三/五**「**本週 GitHub 專案**」：從 Hacker News 高分討論與 GitHub Trending（weekly）蒐集候選，由 Gemini 依口味（prompt 見 `prompts/github-weekly.md`）挑最多 4 個值得認識的專案寫繁中介紹。介紹過的 repo 記在 `state.json`，之後只有在「隔了冷卻期、且發了新的 GitHub Release」時才會以「（更新）」形式再次出現；送模型看過但沒選上的候選也有幾天冷卻，不會反覆回鍋。
- **週四**「**本週 YouTube 精選**」：從 `sources.yaml` 的頻道白名單（大咖訪談 + agent/LLM 工程實務）抓近一週新片，由 Gemini 依口味（prompt 見 `prompts/youtube-weekly.md`）挑最多 3 支寫導讀——幫你決定晚上看哪支，不轉述內容。

**週日整天不發**（`skip_weekdays`），週末的文章自然滾入週一。

## 文章來源

- [Databricks](https://www.databricks.com/blog)
- [Anthropic](https://claude.com/blog)
- [OpenAI News](https://openai.com/news) · [OpenAI Developers](https://developers.openai.com/blog)
- [NVIDIA Developer](https://developer.nvidia.com/blog)
- [Google Research](https://research.google/blog/)
- [Google DeepMind](https://deepmind.google/blog/)
- [Hugging Face](https://huggingface.co/blog)
- [Thinking Machines](https://thinkingmachines.ai/news/)

清單與抓取方式都在 [`sources.yaml`](sources.yaml)，加來源改這一個檔就好。

## 運作方式

```
GitHub Actions schedule 22:17 UTC = 台北 06:17（best-effort，常延遲 1〜4 小時；
  想砍延遲尾端可升級外部排程器打 workflow_dispatch，見下方「排程與觸發」）
  → main.py     前置閘：skip_weekdays（週日不發）、--once-per-day（今天發過就退出）
  → fetch.py    抓 RSS / 爬列表頁，找出新文章（比對 state.json 去重）
  → pulse.py    脈動段：Gemini + Google Search grounding 產「AI 產業脈動」
                （規則在 prompts/industry-pulse.md，含金融×AI 窄門）
  → github_watch.py（一/三/五）HN 高分 + GitHub Trending → Gemini 選題寫「本週 GitHub 專案」
                （選題口味在 prompts/github-weekly.md）
  → youtube_watch.py（週四）頻道白名單 RSS → Gemini 選題寫「本週 YouTube 精選」
                （選題口味在 prompts/youtube-weekly.md）
  → summarize.py 用 Gemini 產繁中結構化摘要（規則在 prompts/blog-digest.md）
  → notify.py   發 Slack + Email（脈動放在最前面）
  → state.json  更新「已看過」清單與 last_run_date，commit 回 repo
```

## 排程與觸發

目前只用 GitHub `schedule`（cron `17 22 * * *`＝台北 06:17）。它是 best-effort：2026-08 實測 59 天中 50 天在台北 06:49–08:06 送達，但平台積壓時可延遲到中午後，且換 cron 分鐘無解；`workflow_dispatch`（API 觸發）不走 schedule 佇列、近乎即時。**想砍掉延遲尾端時**，照下面步驟升級成「外部排程器為主、schedule 當 fallback」——`--once-per-day` guard 已就緒，兩者同日重複會自動擋掉（先成功的贏）。

一次性設定（約 30 分鐘，目前未啟用）：

1. **建 fine-grained PAT**：GitHub → Settings → Developer settings → Fine-grained tokens → 只選這個 repo、Repository permissions 只給 **Actions: Read and write**、期限可設 No expiration。
2. **建 cron-job.org job**（免費帳號即可）：
   - URL：`https://api.github.com/repos/<owner>/tech-blog-watch/actions/workflows/daily.yml/dispatches`
   - Method：`POST`；Body：`{"ref":"main"}`
   - Headers：`Authorization: Bearer <PAT>`、`Accept: application/vnd.github+json`
   - 排程：時區選 Asia/Taipei、每天 06:15（成功回 HTTP 204）
   - 開啟失敗 email 通知當監控
3. cron-job.org 掛掉的日子由 fallback cron（台北 06:17 + GitHub 延遲）接手，信照到、只是晚一點。

## 檔案

| 檔 | 作用 |
|---|---|
| `sources.yaml` | 監看來源 + YouTube 頻道白名單 + 參數（頻率上限、模型、脈動/週段開關與星期） |
| `prompts/blog-digest.md` | 摘要 prompt（**單一事實來源**，改摘要風格改這裡） |
| `prompts/industry-pulse.md` | AI 產業脈動 prompt（**單一事實來源**，改該段風格/範圍改這裡；含金融×AI 窄門） |
| `prompts/finance-ai-pulse.md` | （已停用）舊金融×AI 獨立段 prompt，2026-08 併入產業段，留檔備查 |
| `prompts/github-weekly.md` | 本週 GitHub 專案的選題口味 prompt（**單一事實來源**） |
| `prompts/youtube-weekly.md` | 本週 YouTube 精選的選題口味 prompt（**單一事實來源**） |
| `fetch.py` | RSS / scrape 抓取、trafilatura 內文擷取 |
| `summarize.py` | Gemini 摘要 → 結構化欄位 |
| `pulse.py` | 脈動段：Gemini Grounding with Google Search（每個啟用的段每日各 1 次 grounded query） |
| `github_watch.py` | 每週 GitHub 專案段（非 grounded；蒐集、去重/更新判斷、選題摘要） |
| `youtube_watch.py` | 每週 YouTube 精選段（非 grounded；頻道 RSS 蒐集、去重、選題導讀） |
| `notify.py` | Slack webhook + Gmail SMTP 發送 |
| `main.py` | 串起流程（含 skip_weekdays / --once-per-day 前置閘） |
| `state.json` | 已看過的文章 URL + 昨日脈動列點 + 介紹過的 GitHub repo / YouTube 影片 + last_run_date（每天由 Actions commit 更新） |
| `.github/workflows/daily.yml` | 排程（fallback cron + workflow_dispatch） |

## 需要的 Secrets（設在 GitHub repo → Settings → Secrets and variables → Actions）

| Secret | 說明 |
|---|---|
| `GEMINI_API_KEY` | Gemini API key（摘要用；Google AI Studio 免費 tier） |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook 網址 |
| `SMTP_USER` | 寄件 Gmail 帳號 |
| `SMTP_PASSWORD` | Gmail「應用程式密碼」（非登入密碼） |
| `EMAIL_TO` | 收件信箱 |
| `EMAIL_FROM` | 選填，預設同 `SMTP_USER` |
| `GEMINI_MODEL` | 選填，覆蓋 `sources.yaml` 的 model |
| `SLACK_CHANNEL` / `SLACK_USERNAME` | 選填 |

只設 Slack 或只設 Email 也行 —— 缺哪組就自動略過那個管道。

## 平台開關（不用改 code、不用 commit）

在 GitHub repo → Settings → Secrets and variables → Actions → **Variables** 分頁（注意是 Variables 不是 Secrets）新增：

| Variable | 說明 |
|---|---|
| `SEND_SLACK` | 設 `false` 停發 Slack；刪掉或設 `true` 恢復 |
| `SEND_EMAIL` | 設 `false` 停發 Email；刪掉或設 `true` 恢復 |

在網頁上改值即可，下一次排程（或手動觸發）就生效。本機 `.env` 也吃同名變數。

## 本機測試

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # 填 key
.venv/bin/python main.py --dry-run   # 只抓+摘要+印出，不發送、不寫 state
```

- `--dry-run`：不發送、不寫 state，印出 Slack 內容（每個啟用的脈動段仍會實際打 1 次 Google Search grounding 查詢）
- `--seed`：把目前列表全部標記為已看過（不摘要不發送）
- `--force-github` / `--force-youtube`：不管星期幾都跑該週段（測試用，通常搭配 `--dry-run`）
- `--once-per-day`：同一台北日期已成功跑過（`state.json` 的 `last_run_date`）就直接退出——給 workflow 用，本機測試不用帶
- 首次正式跑（`state.json` 為空）會自動走 seed，避免第一次灌爆整個 backlog

## 調整

- **換模型**：改 `sources.yaml` 的 `model:`（例：`gemini-2.5-flash`、`gemini-2.5-flash-lite`）。或不動檔案、設環境變數/secret `GEMINI_MODEL` 覆蓋。
- **API key**：走 `GEMINI_API_KEY`（本機 `.env`、雲端 GitHub secret），程式沒有寫死任何 key。
- **改頻率/觸發**：主觸發時間在 cron-job.org 的 job 設定；fallback cron 在 `.github/workflows/daily.yml`（UTC）；整天不發的星期在 `sources.yaml` 的 `skip_weekdays`
- **改摘要風格**：`prompts/blog-digest.md`
- **改脈動風格/範圍**：`prompts/industry-pulse.md`（含金融×AI 窄門的鬆緊）；整段關掉：`sources.yaml` 的 `pulse_enabled` 設 `false`
- **改 GitHub 週段**：選題口味改 `prompts/github-weekly.md`；星期幾出、幾個專案、HN 分數門檻、冷卻天數都在 `sources.yaml` 的 `github_weekly_*` / `github_hn_min_points` / `github_refeature_days` / `github_shown_cooldown_days`；關掉設 `github_weekly_enabled: false`
- **改 YouTube 週段**：選題口味改 `prompts/youtube-weekly.md`；頻道白名單在 `sources.yaml` 的 `youtube_channels`（channel_id 要 `UC` 開頭的 canonical id）；星期幾出、幾支、回溯天數在 `youtube_weekly_*` / `youtube_max_age_days`；關掉設 `youtube_weekly_enabled: false`
- **加/減來源**：`sources.yaml`