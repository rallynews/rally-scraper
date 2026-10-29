# Rally News Scraper

Automatically scrapes positive news from 150+ trusted sources every 6 hours using AI filtering.

## 🎯 How It Works

1. **GitHub Action** runs every 6 hours
2. Randomly selects **10 sources** from whitelist
3. **Mistral 7B** filters for genuinely positive stories
4. Extracts: headline, image, first paragraph, URL
5. **AI writes a "Rally summary"** - two or three sentences in Rally's own
   words. Every other text field on a scraped row belongs to the publisher, so
   this is what the article page can show without republishing their reporting
6. **AI generates "Rallying Cry"** - one snappy positive sentence
7. Saves to `news.json` (newest first, keeps 200 articles)
8. Auto-commits to repo

Articles scraped before step 5 existed have no Rally summary. To fill them in,
open **Actions → Backfill Rally summaries → Run workflow**: it asks how many to
do, whether to write or only preview, and optionally a start date. Run it as a
dry run first and read what it produces — each article costs a model call, and
whether the writing is any good is a judgement nobody can automate.

`backfill_rally_summary.py` is the same thing on the command line, for anyone
who prefers it.

## 🎮 Joyscroll games

Every story the scraper publishes also gets three small games for **Joyscroll**,
Rally's second app — each tied to the story, so the app can always say *Read
the story*:

- **one trivia question** (four options, or true/false about 40% of the time),
  written from the article's text and checked by a second model before it is
  published;
- **one Joyword answer** — a positive 5-letter word about the story, for the
  word-guessing game;
- **one Crossjoy mini crossword** (5×5, or an open 4×4), built around words
  from the story, every clue checked by having a model solve it blind.

It is the last thing a scrape does (`joyscroll_games.py`, called after the
articles are saved), and it works from the API's list of recent stories still
missing a piece, so a story that failed in one scrape is tried again in the
next. One that fails twice in a week is left alone; the reasons are in the
`trivia_rejects` table (Studio will show them). **Stories from paywalled
sources get no games**: a game's "Read the story" should lead somewhere the
player can read.

### Free to read

At least half of every run's published stories must come from sources **not**
marked paywalled in Studio ▸ Sources (`editorial_filter.limit_paywalled`). The
search counts only stories that rule would let through toward its target,
skips paywalled candidates while the run already holds more paywalled than
free ones, and the final selection drops the weakest paywalled stories if it
must. A run that can't find enough free stories publishes fewer rather than
break the rule. Metered sources count as free.

**The article's text is fetched once and kept in memory only** — never written
to the database, a log or a file. Questions and clues are written fresh and
link back to the source. When the page can't be read (a paywall, a consent
wall), the question is written from the summary and opening paragraph the
scraper already has.

**Cost is capped:** at most six model calls per story and `JOYSCROLL_MAX_CALLS`
(default 120) per run, plus a 15-minute time limit. The games' summary line in
each scrape's log gives the calls used and what OpenRouter charged for them.
If games are failing, the scrape itself is unaffected.

### Setup (once)

1. **Keys.** Make two random values (e.g. `openssl rand -hex 32`, twice). Put
   them in the website's `api/config.php` as `JOYSCROLL_WORDS_API_KEY` and
   `JOYSCROLL_TRIVIA_API_KEY`, and as repository secrets of the same names
   here (Settings ▸ Secrets and variables ▸ Actions). Without them the games
   step is skipped and says so.
2. **The word bank.** Actions ▸ **Joyscroll word seed** ▸ Run workflow. It
   sorts the 13,511 three-to-five-letter words of the public-domain ENABLE list
   (`words/`) into positive / wholesome / excluded, about 136 calls in all.
   Crosswords are filled from the bank, so run it before relying on them; it
   is resumable, so just run it again if it stops early.
3. **Try it.** Actions ▸ **Joyscroll games (manual run)** with *dry run* ticked
   prints what it would save for a few recent stories without saving anything.

Optional repository **variables** (not secrets): `TRIVIA_WRITER_MODEL`
(default `openai/gpt-4o-mini`), `TRIVIA_CHECKER_MODEL` (default
`mistralai/mistral-small-3.2-24b-instruct` — keep it a different model family
from the writer), `JOYSCROLL_MAX_CALLS`, `JOYSCROLL_SEED_MODEL`.

Tests: `python -m unittest discover tests` (crossword fill and validation, the
checks on what the models write, and a whole story with the models faked).

## 🎬 Joyscroll Joyskies (Bluesky)

`bluesky_clips.py` finds short, joyful videos people have posted publicly on
Bluesky for Joyscroll. While Studio's review setting is *automatic*, what
passes its checks goes live at once (creators can opt out at
rally.news/joyscroll/creators, and readers can report clips); set to *manual*,
it waits in **Studio ▸ News ▸ Joyskies** for a person. Workflow: **Joyscroll Joyskies** (`bluesky-clips.yml`), mode
`find` every 6 hours and `recheck` daily, both dispatched by cron-job.org.

- **Read only**, from its own account, signed in once per run with an app
  password. It never posts, likes, follows, replies or messages anyone.
- **Nothing copied**: the post's text and thumbnail are checked in memory and
  dropped. Rally receives the post's AT-URI, CID and rkey, the author's DID and
  verdicts in our own words — never text, handles, names, avatars or media
  URLs. Studio and the app look everything else up live from Bluesky.
- **Labels win**: anything labelled on the post or the creator's profile is
  turned down, using self-labels and Bluesky's moderation service.
- **Polite**: a second between requests, backing off on 429 and stopping after
  three in a row or any 401/403. Caps per run: 30 searches, 2,500 posts, 40
  candidates, 100 model calls (about $0.10); the log's last lines say what was
  used and why the rest were turned down.
- **Where it looks**: first Bluesky's own Video feed (every post a video;
  `CLIPS_VIDEO_FEED` overrides it), then the saved searches in Studio. Wide
  (landscape) videos are welcome: the app shows them whole.
- **Re-check** (`--recheck`): every approved or live clip is looked up again —
  deleted ones become *gone*, edited ones go back to Studio's Pending list for
  a person (the job never checked the new version), newly labelled ones are
  *removed*.

Secrets: `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD`, `JOYSCROLL_CLIPS_API_KEY`
(plus the existing `NEWS_API_URL`, `OPENROUTER_API_KEY`). Variables:
`CLIPS_TEXT_MODEL`, `CLIPS_VISION_MODEL` (both default `openai/gpt-4o-mini`),
`CLIPS_CONTACT_EMAIL`. Tests: `python -m unittest tests.test_bluesky_clips`.

**Mark the finder as a bot** (once): Actions ▸ **Joyscroll Joyskies — mark
the finder as a bot** (`bluesky-bot-label.yml`, `bluesky_bot_label.py`) adds
Bluesky's `bot` self-label to the finder's profile and changes nothing else.
Running it again does nothing.

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

Every candidate image is fetched before an article is accepted, and has to come
back as a photograph big enough to feature. Candidates are tried in the order a
publisher actually means them: `media:content` from the feed, then an
enclosure, then the page's own social-card tags (`og:image`, `twitter:image`,
`link rel=image_src`), then the largest image in the body, and only last the
feed's `media:thumbnail`. The page's *first* `<img>` is not a candidate in its
own right — on a news page that is the masthead.

A candidate is rejected when it is:

- **dead** — the request fails, 404s, or returns an error page instead of an
  image (Rappler does this often, but it isn't the only one);
- **not a photo** — an SVG, a favicon, or a URL whose path marks it as
  furniture (`logo`, `sprite`, `avatar`, `pixel`, `placeholder`, …);
- **too small** — under 400x225, measured from the image's own header bytes,
  or under 8 KB. That is what catches the 150x150 WordPress thumbnail, the
  1x1 tracking GIF and the publisher's mark;
- **already used** by another story in the run.

Upscaled CDN URLs are verified too, and the original resolution is kept if the
higher-resolution path 404s.

When a story has no image of its own, or all of them are rejected, the scraper
falls back to a library of royalty-free photos in Cloudflare R2 and picks the
one whose **file name** is closest to the story — matched against the
headline, the AI-assigned topics, the category, the countries mentioned and
the summary, in that order of weight. That photo is fetched before it is used,
since the manifest lists names that were in the bucket when it was last built;
if nothing in the library can be fetched, the story is skipped rather than
published with a blank space where the picture goes.

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

The site keeps its own copy of this manifest at
`rally-frontend/images/fallback-images.json`, so a photo that fails to load in
a reader's browser — one this scraper could fetch perfectly well from a server —
is replaced client-side by the same matching. **Re-copy it whenever the manifest
is rebuilt here**, or the site will reach for photos the bucket no longer holds.

### Repairing images already in the database

Articles saved before this check existed may still hold dead URLs:

```bash
python scraper.py --repair-images --dry-run   # report only
python scraper.py --repair-images             # replace the broken ones
```

This writes through the API's `PATCH` handler, which lives in the **frontend**
repo (`rally-frontend/api/news.php` — that is the copy deployed to rally.news;
the one in this repo is a stale duplicate and is not served anywhere).

## ✂️ Article Text

RSS is not reliably plain text. Some feeds send a clean sentence, some wrap it
in `<p>`, and the WordPress ones (BusinessDay, Premium Times) lead with the
publisher's entire `<img srcset=...>` tag. The scraper strips tags and decodes
entities as soon as it reads an entry, so the filter scores words rather than
markup and the row that reaches the database is text.

Where a summary was *only* markup, the article's own first paragraph is stored
as the summary instead, since an `<img>` tag leaves nothing to read.

Rows written before this existed still hold their markup, and the site is not
the only thing that reads them — the RSS feed, the crawlable category pages and
the meta/structured-data tags print what is stored. Clean them in place with:

```bash
python scraper.py --repair-text --dry-run     # report only
python scraper.py --repair-text               # strip the markup
```

Rally Originals are left alone: their HTML is authored in Studio, sanitised
there, and rendered as markup on purpose.

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
