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

Sources live in the **Sources tab of Rally Admin**, so one can be added or
removed without a commit and a deploy. The dashboard's list is what gets
scraped — there is no toggle.

To add a source: open the Sources tab, fill in the name, website, feed and
country, and it is in the next run. To stop scraping one, remove it there.

The `WHITELISTED_SOURCES` / `RSS_FEEDS` / `SOURCE_CONTINENTS` maps still at the
top of `scraper.py` are now only a last resort, used when the API is unreachable
*and* there is no lockfile — a scraper with no sources at all is worse than one
running a stale list. Editing them changes nothing in normal operation.

Whether a feed URL is re-validated before it is fetched follows where it came
from, not a setting: a URL from the dashboard is user input and gets the full
check at every redirect hop, while the built-in maps are reviewed code and are
fetched as they always were.

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
hardcoded in `scraper.py` — lives in **News ▸ Filters** in Rally Studio. A set
of rules is written as:

- **Prompt** — the job. An expert news curator reading trusted international
  feeds for stories that promise hope, reconciliation, peace, development,
  compromise and success, plus the objective rules that hold whatever the
  personality happens to feel: positive news is progress, achievements,
  solutions, help, innovation, recovery and cooperation; it is not
  controversial; and it is not corporations enriching themselves or cutting
  deals with each other that do not benefit humanity.
- **Personality** — the reader the model inhabits while it answers. The same
  story lands differently for different people, and Rally publishes for a
  particular one.
- **Scoring questions** — the statements the model rates from 1 (disagree
  completely) to 10 (agree completely), in that personality's voice.
- **Examples** — headlines with scores, which teach the model the scale. An
  example's score decides whether it reads as a positive or a negative one, so
  the two lists can never contradict each other.
- **Thresholds** — the three numbers below.

A story's score is the **average** of its answers, to one decimal. Still one AI
call per candidate: the model answers every question in a single reply. A story
**qualifies** when

- the average reaches the **fallback cutoff** (default **6.5**, adjustable
  between **6.0 and 9.0**), and
- fewer than **`veto_count`** answers came back as a 1 (default **2**: two
  complete disagreements sink a story however well it did elsewhere).

A story the model gives no usable answer for is rejected too — publishing on an
unparseable reply would mean publishing a story nothing judged.

**Nothing scoring below 5.0 is ever published**, whatever the thresholds say.
That floor is not a setting, and the cutoff band sits above it, so in normal use
it never comes up — it is there so a corrupted config or a stale lockfile cannot
quietly put a 4 on the site. Both this repo and the API enforce it.

The cutoff band is deliberately narrower than the scale: 5 means "moderate", so
a cutoff below 6 would publish news Rally does not consider good, and above 9 so
little qualifies that a run exhausts every feed and still falls short of
`MIN_NEW_ARTICLES`. The scraper enforces the same band, so a stale or tampered
value cannot widen it.

#### How a run is filled

Qualifying is not the same as being published. A run fills itself in two tiers,
best first:

1. **Every** qualifying story scoring the **preferred score** or above (default
   **7.5**) goes in, with no cap. If a run finds twenty at 7.5+, it publishes
   twenty and takes **nothing** from below.
2. Only if that leaves the run short of `MIN_NEW_ARTICLES` does it top up,
   highest score first, from the band between the cutoff and the preferred
   score — and it stops the moment the target is met. That band tops a run up;
   it never bulks one out.

The tiers are applied once the run is over, because how far down the run has to
reach depends on how many strong stories it found. A run that still comes up
short publishes what it has. A run that qualifies **nothing** publishes nothing
and is recorded as a **blank scrape**, which Studio reports on the Filters page
next to the rules that produced it.

**Scores are internal.** They are stored against the article for the dashboard
and appear in no public read: not `news.php`, not the digest, not `article.php`,
not the feed. Readers never see a number attached to a story.

```bash
python editorial_filter.py --show   # print the exact prompt a run would use
```

#### Versions, and why a save is not a deploy

Filter rules are **versioned**. Studio keeps every set it has ever had and
deploys exactly one; `api/filter.php` serves that one, and that is the only one
a scheduled run judges stories by. Saving a draft in Studio changes nothing
here — only pressing Deploy does, and it takes effect on the next run rather
than immediately. A version freezes the first time it is deployed, so "which
rules published this story" always has an answer; to change a live filter you
duplicate it, edit the copy and deploy that.

To try a draft on real feeds first, run the **Run Rally News Scraper** workflow
by hand and put the version id in the `filter_version` input. That run judges
stories by the draft but does not write `filter.lock.json`, so a dry run can
never leave a draft behind as the fallback everything else falls back to.

The per-run rules are unchanged by any of this: a run still aims for
`MIN_NEW_ARTICLES` stories with at least one from every continent, caps each
source at 2 and each category at `MAX_PER_CATEGORY`, and stops early only when
the feeds run dry or the 45-minute clock runs out. Because the filter is now the
most likely reason a run comes up short, every run ends with a summary saying
whether it met its target — how it was filled, how many qualifying stories it
did not need, and if it came up short, how many more each lower cutoff would
have admitted, so the choice between "the filter is too tight" and "the feeds
were quiet" is not a guess. Every run is reported to `api/scrape-report.php`,
which is how Studio can tell a blank scrape from an Action that never fired.

`filter.lock.json` is the fallback, committed on every run like
`sources.lock.json`, so changes to the deployed filter stay visible in git
history. If the API is unreachable and there is no lock, the built-in defaults
reproduce the deployed wording — so the fallback judges stories the same way.

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
- `editorial_filter.py` - Reads the deployed filter version; builds the scoring
  prompt, parses the answers, applies the cutoff, the veto, the 5.0 floor and
  the two-tier fill
- `filter.lock.json` - Last known good deployed filter (auto-updated, don't edit)
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
