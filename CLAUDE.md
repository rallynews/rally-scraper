# Rally Scraper — Claude Code Context

## What this project is

A Python news scraper (`scraper.py`) that runs every 10 minutes via GitHub Actions on the `rallynews/rally-scraper` repo. It scrapes RSS feeds from 31 whitelisted sources, uses AI (OpenRouter) to filter for genuinely positive news, and stores articles.

## Active branch / PR

All changes go to branch `claude/migrate-news-to-database-ZTcL1`, which corresponds to **PR #12** on GitHub. Push to this branch to update the PR.

## Current architecture

### Data flow
```
GitHub Actions (every 10 min)
  → scraper.py
  → POST new articles to https://yoursite.com/api/news.php  (IONOS PHP endpoint)
  → PHP script INSERTs into MariaDB 10.11 on IONOS (localhost connection)
  → Also writes news.json to the repo (committed by workflow)
```

### Why PHP endpoint?
IONOS shared hosting MariaDB is not externally accessible — the hostname `db5020489014.hosting-data.io` only resolves within IONOS's own network. GitHub Actions cannot reach it directly. The PHP script sits on the same IONOS server and can talk to MySQL via localhost.

### Fallback behaviour
If `NEWS_API_URL` / `NEWS_API_KEY` secrets are not set, or the API is unreachable, the scraper falls back to reading/writing `news.json` directly (the old behaviour). news.json is always written regardless.

## Database

- **Host**: `db5020489014.hosting-data.io` (localhost from PHP)
- **Database**: `dbs15689792`
- **User**: `dbu2620088`
- **Type**: MariaDB 10.11 on IONOS shared hosting

### Table schema (`articles`)
```sql
id              INT AUTO_INCREMENT PRIMARY KEY
title           VARCHAR(1000) NOT NULL
source          VARCHAR(200)
url             VARCHAR(2000) NOT NULL  -- UNIQUE KEY (url(767))
content         TEXT                    -- formerly called first_paragraph
summary         TEXT
image_url       VARCHAR(2000)
timestamp       DATETIME
category        VARCHAR(50)             -- climate|transportation|ai|business|politics|entertainment|world|religion|arts
rally_originals TINYINT(1) DEFAULT 0   -- True = written by the site owner; False = scraped
created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```

The table is auto-created by `api/news.php` on first request.

### First-run migration
On the first run (when the DB is empty), `scraper.py` batch-POSTs all existing `news.json` articles to the API. All migrated articles get `rally_originals = 0`.

## PHP API (`api/news.php`)

Deployed to IONOS at `https://yoursite.com/api/news.php`.

| Method | Auth | Behaviour |
|--------|------|-----------|
| GET | none | Returns articles as JSON. Params: `?limit=200`, `?offset=0`, `?category=ai` |
| POST | `X-API-Key` header | Inserts articles. Accepts a single object or array. Returns `{"success": true, "inserted": N}` |

### Config file (NOT in git)
`api/config.php` must be created manually on the IONOS server (copy from `api/config.example.php`). It defines `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASS`, and `API_KEY`. It is gitignored.

## GitHub Actions secrets required

| Secret | Description |
|--------|-------------|
| `OPENROUTER_API_KEY` | Already set. Powers AI filtering/categorisation. |
| `NEWS_API_URL` | URL of the PHP endpoint, e.g. `https://yoursite.com/api/news.php` |
| `NEWS_API_KEY` | Secret key matching `API_KEY` in `api/config.php` on IONOS |

`DB_PASS` is no longer needed (was used for the failed direct PyMySQL approach).

## Key files

| File | Purpose |
|------|---------|
| `scraper.py` | Main scraper. Sections: RSS parser, helper functions, DATABASE API, main scraper loop |
| `api/news.php` | PHP endpoint deployed to IONOS |
| `api/config.example.php` | Template for `api/config.php` (fill in and upload to IONOS, never commit) |
| `.github/workflows/scrape.yml` | Runs every 10 min; commits `news.json`, `balance.json`, `rallyingcry.json` |
| `news.json` | Most recent 200 articles (also committed to repo for GitHub Pages fallback) |
| `balance.json` | Daily paragraph summarising rejected (negative) articles |
| `rallyingcry.json` | Daily one-sentence upbeat summary of approved articles |
| `criteria.txt` | Human-readable filtering rules (not used programmatically) |

## Article dict structure (Python)

```python
{
    'title':           str,
    'source':          str,   # e.g. "BBC News"
    'url':             str,
    'content':         str,   # first paragraph extracted from article page
    'summary':         str,   # first ~300 chars from RSS feed
    'image_url':       str,
    'timestamp':       str,   # ISO 8601, e.g. "2026-05-18T12:00:00Z"
    'category':        str,   # one of the 9 VALID_CATEGORIES
}
```

Note: the field was renamed from `first_paragraph` to `content`. Old news.json entries may still use `first_paragraph` — the PHP endpoint and migration code handle both.

## AI models used (OpenRouter, in fallback order)

1. `mistral/mistral-small-3.2` (primary — cheap, EU-based)
2. `google/gemini-2.0-flash-001`
3. `openai/o1-mini`
4. `openai/gpt-4o-mini`
5. `meta-llama/llama-3.3-70b-instruct`

## Setup still pending (as of this session)

1. **Upload PHP files to IONOS**: create `api/` folder in website root, upload `api/news.php` and a filled-in `api/config.php`
2. **Add GitHub secrets**: `NEWS_API_URL` and `NEWS_API_KEY`
3. **Update the frontend**: currently fetches `news.json` from GitHub Pages — should eventually be updated to fetch from `https://yoursite.com/api/news.php` instead
4. **Merge PR #12** once the above is verified working

## What NOT to do

- Do not attempt a direct PyMySQL connection to `db5020489014.hosting-data.io` — it is not resolvable from outside IONOS's network
- Do not commit `api/config.php` — it contains DB credentials
- Do not push to `main` directly — all changes go to `claude/migrate-news-to-database-ZTcL1`
