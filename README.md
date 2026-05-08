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
   - Or use this one: `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`
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

Edit `scraper.py`, add to `NEWS_SOURCES` dict:

```python
NEWS_SOURCES = {
    ...
    "Your Source": "https://example.com/rss",
}
```

## 📁 Files

- `scraper.py` - Main scraper script
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
