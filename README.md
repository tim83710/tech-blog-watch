# tech-blog-watch

每日自動追蹤大廠技術部落格，有新文章就用**繁體中文**摘要（附原文重點引用），發到 **Slack** 與 **Email**。跑在 **GitHub Actions** 上，不依賴本機開機。

## 監看來源

Databricks、Anthropic、OpenAI (News + Developers)、NVIDIA Developer、Google Research、Google DeepMind、Hugging Face。
清單與抓取方式都在 [`sources.yaml`](sources.yaml)，加來源改這一個檔就好。

## 運作方式

```
GitHub Actions (每天 09:45 台北)
  → fetch.py    抓 RSS / 爬列表頁，找出新文章（比對 state.json 去重）
  → summarize.py 用 Gemini 產繁中結構化摘要（規則在 prompts/blog-digest.md）
  → notify.py   發 Slack + Email
  → state.json  更新「已看過」清單，commit 回 repo
```

## 檔案

| 檔 | 作用 |
|---|---|
| `sources.yaml` | 監看來源 + 參數（頻率上限、模型、內文字元上限） |
| `prompts/blog-digest.md` | 摘要 prompt（**單一事實來源**，改摘要風格改這裡） |
| `fetch.py` | RSS / scrape 抓取、trafilatura 內文擷取 |
| `summarize.py` | Gemini 摘要 → 結構化欄位 |
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

- `--dry-run`：不發送、不寫 state，印出 Slack 內容
- `--seed`：把目前列表全部標記為已看過（不摘要不發送）
- 首次正式跑（`state.json` 為空）會自動走 seed，避免第一次灌爆整個 backlog

## 調整

- **換模型**：改 `sources.yaml` 的 `model:`（例：`gemini-2.5-flash`、`gemini-2.5-flash-lite`）。或不動檔案、設環境變數/secret `GEMINI_MODEL` 覆蓋。
- **API key**：走 `GEMINI_API_KEY`（本機 `.env`、雲端 GitHub secret），程式沒有寫死任何 key。
- **改頻率**：`.github/workflows/daily.yml` 的 cron（UTC）
- **改摘要風格**：`prompts/blog-digest.md`
- **加/減來源**：`sources.yaml`
- **防洪水**：`sources.yaml` 的 `max_per_source` / `max_per_run` / `max_age_days`
