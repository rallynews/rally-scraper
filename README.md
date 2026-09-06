# Rally News Scraper

Automatically scrapes positive news from 150+ trusted sources every 6 hours using AI filtering.

## 🎯 How It Works

1. **GitHub Action** runs every 6 hours
2. Randomly selects **10 sources** from whitelist
3. **Mistral 7B** filters for genuinely positive stories
4. Extracts: headline, image, first paragraph, URL
5. **AI generates "Rallying Cry"** - one snappy positive sentence
6. Saves to `news.json` (newest first, keeps 200 articles)
7. Auto-commits to repo

## 🚀 Setup

### Step 1: Fork This Repo

1. Click "Fork" button on GitHub
2. Clone your fork locally

### Step 2: Add Hugging Face API Key as Secret

**IMPORTANT:** The API key is stored securely in GitHub Secrets, not in the code.

1. Get your Hugging Face API key from: https://huggingface.co/settings/tokens
2. Go to your repo's **Settings** → **Secrets and variables** → **Actions**
3. Click **"New repository secret"**
4. Name: `HUGGINGFACE_API_KEY`
5. Value: (paste your API key)
6. Click **"Add secret"**

### Step 3: Enable GitHub Actions

1. Go to your repo's **Settings** → **Actions** → **General**
2. Under "Workflow permissions", select **Read and write permissions**
3. Click **Save**

### Step 3: Manual First Run

1. Go to **Actions** tab
2. Click "Scrape Positive News"
3. Click **Run workflow** → **Run workflow**
4. Wait 5-10 minutes for first scrape

### Step 4: Enable GitHub Pages

1. Go to **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: **main** / folder: **/ (root)**
4. Click **Save**

Your `news.json` will be available at:
```
https://YOUR-USERNAME.github.io/rally-scraper/news.json
```

### Step 5: Update Frontend

In your `index.html`, update the JSON URL:

```javascript
const NEWS_JSON_URL = 'https://YOUR-USERNAME.github.io/rally-scraper/news.json';
```

## 📊 What Gets Scraped

**Positive Topics:**
- Improving democracy
- Reducing poverty
- Improving education
- Medical advancements
- Positive climate news
- Cultural cooperation
- Tolerance & equity
- Robust courts & peace
- Helpful technology
- Positive children's news
- Compromise & cooperation
- Empowering entertainment
- Positive art & culture

**Excluded:**
- Polarizing/divisive content
- Negative/crisis news
- Neutral reporting

## 🔧 Customization

### Change Scraping Frequency

Edit `.github/workflows/scrape.yml`:

```yaml
on:
  schedule:
    - cron: '0 */6 * * *'  # Every 6 hours
    # Examples:
    # - cron: '0 */3 * * *'  # Every 3 hours
    # - cron: '0 9 * * *'    # Daily at 9 AM UTC
```

### Change Number of Sources Per Run

Edit `scraper.py`, line with `random.sample`:

```python
selected_sources = random.sample(list(NEWS_SOURCES.items()), 15)  # Changed from 10 to 15
```

### Add More News Sources

Sources are moving out of this repository and into the **Sources tab of Rally
Admin**, so a source can be added without a commit and a deploy. The move is
happening in two steps, and right now it is on the first one.

**Shadow mode (current default).** Every run fetches the dashboard's list from
`api/sources.php`, validates it, and prints how it differs from the
`WHITELISTED_SOURCES` / `RSS_FEEDS` / `SOURCE_CONTINENTS` maps in `scraper.py` —
then scrapes those maps anyway. Nothing the dashboard says can change a run.
Read the diff in the Actions log to see whether the two agree.

**Live mode.** Set the `SOURCE_DIRECTORY_MODE` repository variable to `live` and
the dashboard's list is what gets scraped. The maps in `scraper.py` stay as the
seed for the lockfile. Set it back to `shadow` to revert — no code change either
way.

So: to add a source **today**, edit both `WHITELISTED_SOURCES` and `RSS_FEEDS`
(and give it a continent in `SOURCE_CONTINENTS`, or it won't count toward the
per-run coverage check). Once live mode is on, add it in the dashboard instead.

Two secrets are involved:

- `SOURCES_API_KEY` — read-only key for the directory endpoint. Must match
  `SOURCES_API_KEY` in the frontend's `api/config.php`, and must **not** be the
  same value as `NEWS_API_KEY`: that one can write articles and signs the digest
  removal links, and this runner should not hold a secret with that reach.
- `NEWS_API_URL` — already set; `sources.php` is derived from it.

`sources.lock.json` is the last list that was successfully read, committed by
the workflow on every run. It is the fallback when the API is unreachable or
answers with something implausible, and its git history is the record of what
changed in the source list and when. Don't edit it by hand.

```bash
python source_directory.py --check       # fetch, validate, diff against the code
python source_directory.py --verify      # fetch every feed, report the dead ones
python source_directory.py --print-lock  # show the cached list
```

### Changing what counts as good news

The judgement applied to every candidate story — what used to be a prompt
hardcoded in `scraper.py` — lives in the **Filter tab of Rally Admin**. Three
things are editable there:

- **Instructions** — what the model is asked to judge.
- **Examples** — headlines with scores, which teach the model the scale. An
  example's score decides whether it reads as a positive or a negative one, so
  the two lists can never contradict each other.
- **Cutoff** — the lowest score a story can have and still be published.

Every story is scored **1–10**: 10 is the best news imaginable, 5 is moderate, 1
is the worst. This replaced the old YES/NO question rather than adding a second
one, so a run still makes one AI call per candidate. A story that scores below
the cutoff is rejected, and so is one the model gives no usable answer for —
publishing on an unparseable reply would mean publishing a story nothing judged.

**Scores are internal.** They are stored against the article for the dashboard
and appear in no public read: not `news.php`, not the digest, not `article.php`,
not the feed. Readers never see a number attached to a story.

```bash
python editorial_filter.py --show   # print the exact prompt a run would use
```

`filter.lock.json` is the fallback, committed on every run like
`sources.lock.json`, so changes to the filter stay visible in git history. If
the API is unreachable and there is no lock, the built-in defaults reproduce the
prompt that used to be hardcoded — so the fallback behaves the way the scraper
always did.

### Checking that feeds actually work

Validating a URL and validating a *feed* are different jobs. `--check` answers
"is this URL safe and well-formed"; `--verify` answers "does fetching it produce
a feed", which is the only way to catch a URL that is perfectly valid and simply
wrong — an https address on a real domain that now serves a 404 page because the
publisher moved its feed.

Run it from the **Verify Source Feeds** workflow (Actions → Run workflow) after
adding sources in the dashboard, and periodically to catch feeds that rot. It
writes nothing and fails the run if any feed is dead, naming what to fix.

Note that a feed your *browser* downloads instead of displaying is fine. That is
a Content-Type decision by the publisher and a presentation decision by the
browser; the scraper reads the bytes either way, and `--verify` judges a feed
only on whether those bytes parse.

## 🖼️ Featured Images

Every candidate image is checked before an article is accepted — the URL from
the feed, from `og:image`, or from the page's first `<img>` is requested and
must actually return an image. Sources that advertise photos which no longer
resolve (Rappler does this often, but it isn't the only one) no longer sneak a
broken image into the database. Upscaled CDN URLs are verified too, and the
original resolution is kept if the higher-resolution path 404s.

When a story has no image of its own, or all of them are dead, the scraper
falls back to a library of royalty-free photos in Cloudflare R2 and picks the
one whose **file name** is closest to the story — matched against the
headline, the AI-assigned topics, the category, the countries mentioned and
the summary, in that order of weight.

A photo may be used again on a later day, but never twice on the same day — so
a day's stories never show the same stock photo side by side, while the library
still gets full use over time.

### Managing the photo library

The file names live in `fallback_images.json` (204 photos as committed). R2's
public `r2.dev` domain serves objects but won't list them, so the manifest is
the source of truth — rebuild it whenever photos are added or removed:

```bash
# Option 1 — read the bucket over the S3 API (needs R2 API credentials)
export R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=...
python image_library.py --refresh

# Option 2 — from any directory listing you can produce
rclone lsf r2:your-bucket/videos | python image_library.py --import -
aws s3 ls s3://your-bucket/videos/ | python image_library.py --import -
python image_library.py --import pasted-listing.txt   # or paste from the dashboard
```

Any format works — XML, HTML, JSON or plain text; the file names are all that
gets read. To see what a headline would be given:

```bash
python image_library.py --match "Divers replant coral on a dying reef"
```

Descriptive file names make the matching much better: `coral-reef-divers.jpg`
matches a reef story, `IMG_0042.jpg` can only ever be a random pick.

Bucket and folder default to the values in `image_library.py` and can be
overridden with `FALLBACK_IMAGE_BASE_URL` and `FALLBACK_IMAGE_PREFIX`.

### Repairing images already in the database

Articles saved before this check existed may still hold dead URLs:

```bash
python scraper.py --repair-images --dry-run   # report only
python scraper.py --repair-images             # replace the broken ones
```

This writes through the API's `PATCH` handler (added in `api/news.php`, so
re-upload that file to your host first), or straight to `news.json` when the
API isn't configured.

## 📁 Files

- `scraper.py` - Main scraper script
- `source_directory.py` - Reads the admin-managed source list; URL safety checks
- `sources.lock.json` - Last known good source list (auto-updated, don't edit)
- `editorial_filter.py` - Reads the admin-managed filter; builds the scoring prompt
- `filter.lock.json` - Last known good filter (auto-updated, don't edit)
- `image_library.py` - Default featured images: manifest, matching, link checks
- `fallback_images.json` - File names of the royalty-free photo library
- `.github/workflows/scrape.yml` - GitHub Action config
- `requirements.txt` - Python dependencies
- `news.json` - Generated article database (auto-updated)

## 🐛 Troubleshooting

**"No articles found"**
- AI is being too strict. First run may find fewer articles.
- Try manual run to trigger immediately

**"API errors"**
- Hugging Face free tier has rate limits
- Scraper has built-in retry logic

**"Action failed"**
- Check Actions tab for error logs
- Ensure you enabled write permissions

## 📄 Article Format

```json
{
  "title": "Original headline from source",
  "source": "Source name",
  "url": "Original article URL",
  "first_paragraph": "First ~500 chars of article",
  "rallying_cry": "Short AI-generated positive summary",
  "image_url": "Article image or fallback",
  "timestamp": "2026-05-08T12:00:00Z",
  "category": "home"
}
```

## 💡 Tips

- First scrape takes longest (models loading)
- Subsequent scrapes are faster
- 10 sources × 5 articles each = up to 50 new articles per run
- AI filtering reduces this to ~10-20 genuinely positive stories
- Database keeps 200 most recent articles

## 🌱 Philosophy

Rally believes in constructive journalism - news that informs, inspires, and empowers rather than divides or demoralizes. We curate from trusted sources worldwide, using AI to surface stories about human progress, cooperation, and positive change.
