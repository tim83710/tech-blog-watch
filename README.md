# tech-blog-watch

每日自動追蹤各大科技廠商技術部落格，有新文章就用**繁體中文**摘要（附原文重點引用），發到 **Slack** 與 **Email**。跑在 **GitHub Actions** 上，不依賴本機開機。

每日 digest 開頭另附「**AI 產業脈動**」快訊列點：用 Gemini 的 Google Search grounding 即時搜尋、總結過去 24 小時大廠 blog 以外的產業大事（新勢力模型、爆紅開源專案、主權 AI、晶片、融資、監管），列點呈現、來源連結附在後面；大廠 blog 沒新文章的日子也會單獨發這一段。

## 文章來源

- [Databricks](https://www.databricks.com/blog)
- [Anthropic](https://claude.com/blog)
- [OpenAI News](https://openai.com/news) · [OpenAI Developers](https://developers.openai.com/blog)
- [NVIDIA Developer](https://developer.nvidia.com/blog)
- [Google Research](https://research.google/blog/)
- [Google DeepMind](https://deepmind.google/blog/)
- [Hugging Face](https://huggingface.co/blog)

清單與抓取方式都在 [`sources.yaml`](sources.yaml)，加來源改這一個檔就好。

## 運作方式

```
GitHub Actions (每天 06:30 台北排程；實際常因 GitHub 排程延遲而晚到)
  → fetch.py    抓 RSS / 爬列表頁，找出新文章（比對 state.json 去重）
  → pulse.py    「AI 產業脈動」：Gemini + Google Search grounding 產一段產業快訊（規則在 prompts/industry-pulse.md）
  → summarize.py 用 Gemini 產繁中結構化摘要（規則在 prompts/blog-digest.md）
  → notify.py   發 Slack + Email（脈動放在最前面）
  → state.json  更新「已看過」清單，commit 回 repo
```

## 檔案

| 檔 | 作用 |
|---|---|
| `sources.yaml` | 監看來源 + 參數（頻率上限、模型、內文字元上限、脈動開關） |
| `prompts/blog-digest.md` | 摘要 prompt（**單一事實來源**，改摘要風格改這裡） |
| `prompts/industry-pulse.md` | 產業脈動 prompt（**單一事實來源**，改脈動風格/範圍改這裡） |
| `fetch.py` | RSS / scrape 抓取、trafilatura 內文擷取 |
| `summarize.py` | Gemini 摘要 → 結構化欄位 |
| `pulse.py` | 產業脈動：Gemini Grounding with Google Search（免費 tier 5,000 次/月，每日只用 1 次） |
| `notify.py` | Slack webhook + Gmail SMTP 發送 |
| `main.py` | 串起流程 |
| `state.json` | 已看過的文章 URL（每天由 Actions commit 更新） |
| `.github/workflows/daily.yml` | 排程 |

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

## 本機測試

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # 填 key
.venv/bin/python main.py --dry-run   # 只抓+摘要+印出，不發送、不寫 state
```

- `--dry-run`：不發送、不寫 state，印出 Slack 內容（「AI 產業脈動」啟用時仍會實際打 1 次 Google Search grounding 查詢）
- `--seed`：把目前列表全部標記為已看過（不摘要不發送）
- 首次正式跑（`state.json` 為空）會自動走 seed，避免第一次灌爆整個 backlog

## 調整

- **換模型**：改 `sources.yaml` 的 `model:`（例：`gemini-2.5-flash`、`gemini-2.5-flash-lite`）。或不動檔案、設環境變數/secret `GEMINI_MODEL` 覆蓋。
- **API key**：走 `GEMINI_API_KEY`（本機 `.env`、雲端 GitHub secret），程式沒有寫死任何 key。
- **改頻率**：`.github/workflows/daily.yml` 的 cron（UTC）
- **改摘要風格**：`prompts/blog-digest.md`
- **改產業脈動風格/範圍**：`prompts/industry-pulse.md`；關掉整段：`sources.yaml` 的 `pulse_enabled: false`
- **加/減來源**：`sources.yaml`