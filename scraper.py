#!/usr/bin/env python3
"""
Rally News Scraper - Completely Rebuilt
Only scrapes positive news from whitelisted sources within last 48 hours
"""

import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import time
import os
import pymysql
import pymysql.cursors

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')

DB_HOST = 'db5020489014.hosting-data.io'
DB_PORT = 3306
DB_NAME = 'dbs15689792'
DB_USER = 'dbu2620088'
DB_PASS = os.environ.get('DB_PASS')

# Strict whitelist - ONLY these sources allowed
WHITELISTED_SOURCES = {
    'BBC News', 'The Guardian', 'Reuters', 'NPR', 'Al Jazeera',
    'The New York Times', 'The Washington Post', 'The Atlantic',
    'Scientific American', 'Nature News', 'Science News', 'Wired',
    'TechCrunch', 'Ars Technica', 'MIT Technology Review',
    'The Wall Street Journal', 'Bloomberg', 'CNBC',
    'Los Angeles Times', 'The Japan Times', 'The Straits Times',
    'The Sydney Morning Herald', 'The Globe and Mail',
    'Le Monde', 'DW (Deutsche Welle)', 'The Telegraph',
    'Grist', 'Science', 'New Scientist', 'Newsweek'
}

# RSS feeds for whitelisted sources
RSS_FEEDS = {
    'BBC News': 'http://feeds.bbci.co.uk/news/rss.xml',
    'The Guardian': 'https://www.theguardian.com/world/rss',
    'Reuters': 'https://www.reuters.com/rssFeed/worldNews',
    'NPR': 'https://feeds.npr.org/1001/rss.xml',
    'Al Jazeera': 'https://www.aljazeera.com/xml/rss/all.xml',
    'The New York Times': 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',
    'The Washington Post': 'https://feeds.washingtonpost.com/rss/world',
    'The Atlantic': 'https://www.theatlantic.com/feed/all/',
    'The Wall Street Journal': 'https://feeds.a.dj.com/rss/RSSWorldNews.xml',
    'CNBC': 'https://www.cnbc.com/id/100003114/device/rss/rss.html',
    'Scientific American': 'http://rss.sciam.com/ScientificAmerican-Global',
    'Nature News': 'http://feeds.nature.com/nature/rss/current',
    'Science News': 'https://www.sciencenews.org/feed',
    'Science': 'https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science',
    'Wired': 'https://www.wired.com/feed/rss',
    'TechCrunch': 'https://techcrunch.com/feed/',
    'Ars Technica': 'http://feeds.arstechnica.com/arstechnica/index',
    'MIT Technology Review': 'https://www.technologyreview.com/feed/',
    'Los Angeles Times': 'https://www.latimes.com/world-nation/rss2.0.xml',
    'The Japan Times': 'https://www.japantimes.co.jp/feed/topstories/',
    'The Straits Times': 'https://www.straitstimes.com/news/singapore/rss.xml',
    'The Sydney Morning Herald': 'https://www.smh.com.au/rss/feed.xml',
    'The Globe and Mail': 'https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/world/',
    'Le Monde': 'https://www.lemonde.fr/en/rss/une.xml',
    'DW (Deutsche Welle)': 'https://rss.dw.com/rdf/rss-en-all',
    'The Telegraph': 'https://www.telegraph.co.uk/rss.xml',
    'Grist': 'https://grist.org/feed/',
    'New Scientist': 'https://www.newscientist.com/subject/technology/feed/',
    'Newsweek': 'https://www.newsweek.com/rss'
}

# Valid categories (AI will categorize into these)
VALID_CATEGORIES = [
    'climate',        # Environment, sustainability, renewable energy
    'transportation', # Transit, infrastructure, mobility
    'ai',            # Technology, science, research, innovation
    'business',      # Economy, finance, companies, startups
    'politics',      # Government, policy, legislation, elections
    'entertainment', # Film, music, celebrity, sports, TV
    'world',         # International news, diplomacy, global affairs
    'religion',      # Faith, spirituality, religious leaders
    'arts'           # Culture, literature, books, museums, theater
]

# Free Gemini first, then paid o1-mini, then stable fallbacks
AI_MODELS = [
    'mistral/mistral-small-3.2',            # cheap, Europe-based, hits first
    'google/gemini-2.0-flash-001',          # Gemini 2.0 Flash (stable release)
    'openai/o1-mini',                        # o1-mini (stable ID, no dated suffix)
    'openai/gpt-4o-mini',                    # fallback: reliable and cheap
    'meta-llama/llama-3.3-70b-instruct',    # fallback: strong open model
]

# ═══════════════════════════════════════════════════════════════
# RSS PARSER (stdlib only — no feedparser dependency)
# ═══════════════════════════════════════════════════════════════

def parse_feed(url, timeout=15):
    """Fetch and parse an RSS or Atom feed; return list of entry dicts."""
    try:
        resp = requests.get(url, timeout=timeout, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; RallyNewsBot/1.0)'
        })
        resp.raise_for_status()
    except Exception as e:
        print(f"  Feed fetch error: {e}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        print(f"  Feed parse error: {e}")
        return []

    MEDIA_NS = 'http://search.yahoo.com/mrss/'

    def local(el):
        return el.tag.split('}', 1)[-1] if '}' in el.tag else el.tag

    def find_local(parent, name):
        return next((c for c in parent if local(c) == name), None)

    def findall_local(parent, name):
        return [c for c in parent if local(c) == name]

    def elem_text(parent, *path):
        node = parent
        for step in path:
            node = find_local(node, step)
            if node is None:
                return ''
        return (node.text or '').strip()

    def parse_entry(item, is_atom):
        entry = {}
        if is_atom:
            entry['title'] = elem_text(item, 'title')
            for link_el in findall_local(item, 'link'):
                href = link_el.get('href', '')
                if href:
                    entry.setdefault('link', href)
                    if link_el.get('rel', 'alternate') == 'alternate':
                        entry['link'] = href
                        break
            summary_el = find_local(item, 'summary')
            if summary_el is None:
                summary_el = find_local(item, 'content')
            entry['summary'] = (summary_el.text or '').strip() if summary_el is not None else ''
            pub_el = find_local(item, 'published')
            if pub_el is None:
                pub_el = find_local(item, 'updated')
            entry['published_parsed'] = (pub_el.text or '').strip() if pub_el is not None else None
        else:
            entry['title'] = elem_text(item, 'title')
            link_el = find_local(item, 'link')
            entry['link'] = (link_el.text or '').strip() if link_el is not None else ''
            if not entry['link']:
                guid_el = find_local(item, 'guid')
                if guid_el is not None:
                    val = (guid_el.text or '').strip()
                    if val.startswith('http'):
                        entry['link'] = val
            desc_el = find_local(item, 'description')
            entry['summary'] = (desc_el.text or '').strip() if desc_el is not None else ''
            pub_el = find_local(item, 'pubDate')
            if pub_el is None:
                pub_el = find_local(item, 'date')
            entry['published_parsed'] = (pub_el.text or '').strip() if pub_el is not None else None

        entry.setdefault('link', '')

        media_content = [{'url': el.get('url')} for el in item
                         if el.tag == f'{{{MEDIA_NS}}}content' and el.get('url')]
        if media_content:
            entry['media_content'] = media_content

        media_thumbnail = [{'url': el.get('url')} for el in item
                           if el.tag == f'{{{MEDIA_NS}}}thumbnail' and el.get('url')]
        if media_thumbnail:
            entry['media_thumbnail'] = media_thumbnail

        enclosures = [{'href': el.get('url', ''), 'type': el.get('type', '')}
                      for el in item if local(el) == 'enclosure' and el.get('url')]
        if enclosures:
            entry['enclosures'] = enclosures

        return entry

    root_local = local(root)
    if root_local == 'feed':
        return [parse_entry(e, is_atom=True) for e in findall_local(root, 'entry')]
    else:
        channel = find_local(root, 'channel')
        parent = channel if channel is not None else root
        return [parse_entry(item, is_atom=False) for item in findall_local(parent, 'item')]


# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def is_recent(article_date):
    """Check if article is within last 48 hours"""
    if not article_date:
        return False

    try:
        pub_date = None
        if isinstance(article_date, str):
            # Try RFC 2822 (handles GMT, +0000, etc.)
            try:
                pub_date = parsedate_to_datetime(article_date)
            except Exception:
                pass
            # Try ISO 8601 variants
            if pub_date is None:
                for fmt in ['%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d']:
                    try:
                        pub_date = datetime.strptime(article_date, fmt)
                        break
                    except Exception:
                        continue
            if pub_date is None:
                return False
        else:
            pub_date = datetime(*article_date[:6])

        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=datetime.now().astimezone().tzinfo)

        cutoff = datetime.now(pub_date.tzinfo) - timedelta(hours=48)
        return pub_date > cutoff
    except Exception:
        return False

def call_ai(prompt, timeout=15):
    """Call OpenRouter API with multi-model fallback"""
    if not OPENROUTER_API_KEY:
        print("ERROR: OPENROUTER_API_KEY not set")
        return None
    
    for model in AI_MODELS:
        try:
            response = requests.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {OPENROUTER_API_KEY}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': model,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 100
                },
                timeout=timeout
            )
            
            if response.status_code == 200:
                result = response.json()['choices'][0]['message']['content'].strip()
                print(f"✓ Model {model} succeeded")
                return result
            else:
                print(f"✗ Model {model} failed: {response.status_code}")
                continue
                
        except Exception as e:
            print(f"✗ Model {model} error: {str(e)}")
            continue
    
    print("ERROR: All AI models failed")
    return None

def call_ai_long(prompt, max_tokens=500, timeout=30):
    """Call OpenRouter API with multi-model fallback, for longer prose responses"""
    if not OPENROUTER_API_KEY:
        print("ERROR: OPENROUTER_API_KEY not set")
        return None

    for model in AI_MODELS:
        try:
            response = requests.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {OPENROUTER_API_KEY}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': model,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': max_tokens
                },
                timeout=timeout
            )

            if response.status_code == 200:
                result = response.json()['choices'][0]['message']['content'].strip()
                print(f"✓ Model {model} succeeded")
                return result
            else:
                print(f"✗ Model {model} failed: {response.status_code}")
                continue

        except Exception as e:
            print(f"✗ Model {model} error: {str(e)}")
            continue

    print("ERROR: All AI models failed")
    return None

def generate_balance(rejected_articles):
    """Summarise rejected (negative/neutral) articles into one plain paragraph."""
    if not rejected_articles:
        return None

    articles_text = '\n'.join(
        f"- {a['title']}: {a['summary'][:200]}"
        for a in rejected_articles[:50]
    )

    prompt = f"""You have read the following news articles that were deemed negative or neutral today. Write a single, concise paragraph summarising your findings. Use simple, plain language and be straightforward. Everything must be truthful and based only on what these articles actually say — do not invent or extrapolate.

Articles:
{articles_text}

Write ONE paragraph only. No headers, no bullet points. Be factual and direct."""

    return call_ai_long(prompt, max_tokens=400, timeout=30)

def generate_rallying_cry(approved_articles):
    """Create a one-sentence upbeat summary of today's positive articles."""
    if not approved_articles:
        return None

    articles_text = '\n'.join(
        f"- {a['title']}"
        for a in approved_articles[:20]
    )

    prompt = f"""You have the following positive news headlines. Write a single, upbeat, conversational one-sentence summary of today's good news, mentioning 2–4 specific stories naturally.

Style examples (do not copy these, they are just to show the tone and structure):
- "A new hydropower startup got funded for 2.5 million, Warsaw government recognizes gay marriage, and six new delicious recipes to try this year."
- "A new, reform-minded Prime Minister promises change in India, and global child hunger drops to its lowest ever."
- "Remembering the life and works of Alan Rickman, and a new airport opens its doors in Rio."

Headlines:
{articles_text}

Write ONE sentence only. Be specific, conversational, and uplifting. Do not use quotation marks around the sentence."""

    return call_ai_long(prompt, max_tokens=200, timeout=30)

def is_positive_news(title, summary):
    """Use AI to determine if article is genuinely positive news"""
    prompt = f"""Is this article about POSITIVE news (progress, achievements, solutions, help, innovation, recovery, cooperation)? Positive news is not controversial, and is actively showing prog[...]

Examples of positive news stories:
- A Single Infusion Could Suppress H.I.V. for Years, Study Suggests
- A Writer With a Healthy Appetite, and a Love of New York City
- Worksite testing AI to provide early high heat alerts to keep workers safe
- Innovation abounds in device charging
- Sharp drop in 'forever chemicals' in seabird eggs hailed as win for regulation
- How Japan created the ultimate take-away food
- Macron announces €23 billion of investment at Africa summit
- How a Hollywood star's photos inspired The Waterboys' latest album
- A year after his death, we look back at the legacy of David Bowe.

Examples of negative news stories:
- Kennedy Is Driving a Vast Inquiry Into Vaccines, Despite His Public Silence
- Inside the Israeli Voting Controversy That Engulfed Eurovision
- Reflecting Pool Costs Balloon to $13.1 Million, Records Show
- Man Charged With Assassination Attempt at Press Gala Pleads Not Guilty
- American Passengers Exposed to Hantavirus Begin Quarantine in U.S.
- Emissions rise by 10% over last year, according to new data

Title: {title}
Summary: {summary}

Rules:
- YES only if it's genuinely positive/uplifting
- NO if it's neutral, negative, explanatory, or just informational
- NO if it's about problems, conflicts, crises, or disasters
- NO if it's an explainer or educational content
- NO if it's about controversy or debate

Answer ONLY: YES or NO"""
    
    result = call_ai(prompt)
    return result and 'YES' in result.upper()

def is_duplicate_topic(new_title, new_summary, recent_articles):
    """Check if this article is about the same topic as recent articles"""
    # Compare with last 20 articles to detect duplicate topics
    for article in recent_articles[:20]:
        prompt = f"""Are these two articles about the SAME topic/event/story?

Article 1:
Title: {new_title}
Summary: {new_summary}

Article 2:
Title: {article['title']}
Summary: {article.get('summary', article.get('content', ''))[:300]}

Rules:
- YES if they're about the same specific event, announcement, or story
- YES if one is a follow-up to the other
- NO if they're just in the same general category
- NO if they're about different aspects of a broader topic

Examples of SAME topic:
- "Nvidia invests $40B in AI" vs "Nvidia embraces AI investor role" → YES (same investment)
- "Hungary elects new PM" vs "Peter Magyar sworn in as PM" → YES (same event)

Examples of DIFFERENT topics:
- "NASA Mars rover" vs "SpaceX launches satellite" → NO (different space stories)
- "NYC rent freeze" vs "LA housing policy" → NO (different cities)

Answer ONLY: YES or NO"""
        
        result = call_ai(prompt, timeout=10)
        if result and 'YES' in result.upper():
            print(f"    ✗ Duplicate topic of: {article['title'][:60]}...")
            return True
    
    return False

def categorize_article(title, summary):
    """Determine article category using AI"""
    prompt = f"""Categorize this article into ONE category:

Title: {title}
Summary: {summary}

Categories:
- climate (environment, sustainability, renewable energy, conservation, emissions)
- transportation (transit, infrastructure, mobility, trains, subways, roads)
- ai (technology, science, research, innovation, space, computing)
- business (economy, finance, companies, startups, trade, investments)
- politics (government, policy, legislation, elections, democracy, parliament)
- entertainment (film, music, celebrity, sports, games, TV, events)
- world (international news, diplomacy, global affairs, conflicts, peace)
- religion (faith, spirituality, churches, religious leaders)
- arts (culture, literature, books, museums, theater, visual arts)

Answer with ONLY the category name (one word)."""
    
    result = call_ai(prompt, timeout=10)
    
    if result:
        category = result.strip().lower()
        # Validate it's a real category
        if category in VALID_CATEGORIES:
            return category
    
    # Fallback to world if AI fails or returns invalid category
    return 'world'

def extract_first_paragraph(url):
    """Extract first paragraph from article"""
    try:
        response = requests.get(url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; RallyNewsBot/1.0)'
        })
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Try common paragraph selectors
        for selector in ['article p', '.article-body p', '.story-body p', 'p']:
            paragraphs = soup.select(selector)
            for p in paragraphs:
                text = p.get_text().strip()
                if len(text) > 100:  # Substantial paragraph
                    return text[:500]
        
        return None
    except:
        return None

def get_article_image(entry, used_images):
    """Extract unique image URL from article"""
    # Try media content
    if entry.get('media_content'):
        img = entry['media_content'][0].get('url')
        if img and img not in used_images:
            return img

    # Try media thumbnail
    if entry.get('media_thumbnail'):
        img = entry['media_thumbnail'][0].get('url')
        if img and img not in used_images:
            return img

    # Try enclosures
    if entry.get('enclosures'):
        for enc in entry['enclosures']:
            if 'image' in enc.get('type', ''):
                img = enc.get('href')
                if img and img not in used_images:
                    return img
    
    # Fallback: fetch from page
    try:
        article_url = entry.get('link', '')
        if not article_url:
            return None
            
        response = requests.get(article_url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; RallyNewsBot/1.0)'
        })
        soup = BeautifulSoup(response.text, 'html.parser')

        # Try og:image
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img = og_image['content']
            if img not in used_images:
                return img

        # Try first img tag
        img_tag = soup.find('img', src=True)
        if img_tag:
            img = urljoin(article_url, img_tag['src'])
            if img not in used_images:
                return img
    except:
        pass
    
    return None

# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════

def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )

def ensure_table_exists(conn):
    with conn.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                title           VARCHAR(1000) NOT NULL,
                source          VARCHAR(200),
                url             VARCHAR(2000) NOT NULL,
                content         TEXT,
                summary         TEXT,
                image_url       VARCHAR(2000),
                timestamp       DATETIME,
                category        VARCHAR(50),
                rally_originals TINYINT(1) NOT NULL DEFAULT 0,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_url (url(767))
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
    conn.commit()

def load_existing_from_db(conn):
    """Return (list of article dicts, set of used image URLs) for the last 200 articles."""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT title, source, url, summary, image_url, category, timestamp
            FROM articles
            ORDER BY timestamp DESC
            LIMIT 200
        """)
        rows = cursor.fetchall()
    used_images = {r['image_url'] for r in rows if r.get('image_url')}
    return list(rows), used_images

def migrate_from_json(conn):
    """One-time migration: insert all news.json articles into the DB if the table is empty."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) as count FROM articles")
        if cursor.fetchone()['count'] > 0:
            return

    try:
        with open('news.json', 'r') as f:
            articles = json.load(f)
    except FileNotFoundError:
        print("No news.json found for migration — starting fresh")
        return

    if not articles:
        return

    print(f"First run: migrating {len(articles)} articles from news.json to database...")
    with conn.cursor() as cursor:
        for article in articles:
            try:
                cursor.execute("""
                    INSERT IGNORE INTO articles
                        (title, source, url, content, summary, image_url, timestamp, category, rally_originals)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0)
                """, (
                    article.get('title', ''),
                    article.get('source', ''),
                    article.get('url', ''),
                    article.get('first_paragraph', article.get('content', '')),
                    article.get('summary', ''),
                    article.get('image_url', ''),
                    article.get('timestamp', datetime.now().isoformat()),
                    article.get('category', 'world'),
                ))
            except Exception as e:
                print(f"  Warning: could not migrate '{article.get('title', '')[:60]}': {e}")
    conn.commit()
    print("✓ Migration complete")

def save_articles_to_db(conn, articles):
    """Insert new articles; silently skips duplicates. Returns count inserted."""
    inserted = 0
    with conn.cursor() as cursor:
        for article in articles:
            try:
                cursor.execute("""
                    INSERT IGNORE INTO articles
                        (title, source, url, content, summary, image_url, timestamp, category, rally_originals)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0)
                """, (
                    article.get('title', ''),
                    article.get('source', ''),
                    article.get('url', ''),
                    article.get('content', ''),
                    article.get('summary', ''),
                    article.get('image_url', ''),
                    article.get('timestamp', datetime.now().isoformat()),
                    article.get('category', 'world'),
                ))
                if cursor.rowcount > 0:
                    inserted += 1
            except Exception as e:
                print(f"  Warning: could not insert '{article.get('title', '')[:60]}': {e}")
    conn.commit()
    return inserted

def fetch_recent_articles(conn, limit=200):
    """Fetch full articles from DB for news.json export."""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT title, source, url, content, summary, image_url, timestamp, category, rally_originals
            FROM articles
            ORDER BY timestamp DESC
            LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
    # Convert datetime objects to ISO strings for JSON serialisation
    for row in rows:
        if isinstance(row.get('timestamp'), datetime):
            row['timestamp'] = row['timestamp'].isoformat() + 'Z'
    return rows

# ═══════════════════════════════════════════════════════════════
# MAIN SCRAPER
# ═══════════════════════════════════════════════════════════════

def scrape_news():
    """Main scraping function"""
    print("═" * 60)
    print("RALLY NEWS SCRAPER - Starting")
    print("═" * 60)

    SCRAPE_TIMEOUT = 45 * 60   # 45 minutes max per run
    MIN_NEW_ARTICLES = 6        # target per run
    BATCH_SIZE = 10             # feed entries examined per pass

    start_time = time.time()

    # Load existing articles from database
    existing_articles = []
    used_images = set()
    existing_urls = set()
    db_conn = None

    try:
        db_conn = get_db_connection()
        ensure_table_exists(db_conn)
        migrate_from_json(db_conn)
        existing_articles, used_images = load_existing_from_db(db_conn)
        existing_urls = {a['url'] for a in existing_articles}
        print(f"Loaded {len(existing_articles)} existing articles from database")
    except Exception as e:
        print(f"Warning: Could not connect to database ({type(e).__name__}): {e}")

    new_articles = []
    rejected_articles = []   # negative/neutral articles collected for balance.json
    checked_urls = set()     # URLs already evaluated this run (across all passes)
    pass_num = 0

    while True:
        elapsed = time.time() - start_time

        if elapsed >= SCRAPE_TIMEOUT:
            print(f"\nTimeout reached after {pass_num} passes ({elapsed/60:.1f} min)")
            break

        if len(new_articles) >= MIN_NEW_ARTICLES:
            print(f"\nTarget reached: {len(new_articles)} new articles found")
            break

        start_idx = pass_num * BATCH_SIZE
        end_idx = start_idx + BATCH_SIZE

        print(f"\n{'─' * 60}")
        print(f"Pass {pass_num + 1}: checking feed entries {start_idx + 1}–{end_idx}")
        print(f"Articles found so far: {len(new_articles)}/{MIN_NEW_ARTICLES}")
        print(f"{'─' * 60}")

        new_candidates_this_pass = 0

        for source_name, feed_url in RSS_FEEDS.items():
            if source_name not in WHITELISTED_SOURCES:
                continue

            if time.time() - start_time >= SCRAPE_TIMEOUT:
                break

            print(f"\nScraping: {source_name}")

            try:
                entries = parse_feed(feed_url)

                for entry in entries[start_idx:end_idx]:
                    url = entry.get('link', '').strip()
                    if not url or url in checked_urls:
                        continue

                    checked_urls.add(url)
                    new_candidates_this_pass += 1

                    pub_date = entry.get('published_parsed')
                    if not is_recent(pub_date):
                        continue

                    title = entry.get('title', '').strip()
                    summary = entry.get('summary', entry.get('description', '')).strip()

                    if not all([title, url]):
                        continue

                    if url in existing_urls:
                        continue

                    # Cap: no more than 2 new articles per source per run
                    source_count = sum(1 for a in new_articles if a['source'] == source_name)
                    if source_count >= 2:
                        print(f"    ✗ Already have 2 articles from {source_name} this run")
                        continue

                    print(f"  Checking: {title[:60]}...")
                    if not is_positive_news(title, summary):
                        print(f"    ✗ Not positive news")
                        rejected_articles.append({'title': title, 'summary': summary[:300]})
                        continue

                    print(f"    ✓ Positive news!")

                    combined_articles = new_articles + existing_articles
                    if is_duplicate_topic(title, summary, combined_articles):
                        continue

                    print(f"    ✓ Unique topic!")

                    image_url = get_article_image(entry, used_images)
                    if not image_url:
                        print(f"    ✗ No unique image found")
                        continue

                    used_images.add(image_url)

                    content = extract_first_paragraph(url)
                    if not content:
                        content = summary[:500]

                    category = categorize_article(title, summary)
                    print(f"    ✓ Categorized as: {category}")

                    article = {
                        'title': title,
                        'source': source_name,
                        'url': url,
                        'content': content,
                        'summary': summary[:300] if summary else content[:300],
                        'image_url': image_url,
                        'timestamp': datetime.now().isoformat() + 'Z',
                        'category': category
                    }

                    new_articles.append(article)
                    print(f"    ✓ Added ({len(new_articles)}/{MIN_NEW_ARTICLES})")

                    time.sleep(2)

            except Exception as e:
                print(f"  ✗ Error scraping {source_name}: {str(e)}")
                continue

        pass_num += 1

        # No new URLs found anywhere — all feeds exhausted at this depth
        if new_candidates_this_pass == 0:
            print(f"\nNo new entries found in pass {pass_num}. Feeds exhausted.")
            break

    # Save new articles to database, then regenerate news.json from DB
    if db_conn:
        if new_articles:
            saved = save_articles_to_db(db_conn, new_articles)
            print(f"\nSaved {saved} new articles to database")
        else:
            print("\nNo new articles to save")

        articles_for_json = fetch_recent_articles(db_conn)
        with open('news.json', 'w') as f:
            json.dump(articles_for_json, f, indent=2)
        print("✓ news.json updated from database")
        db_conn.close()
    else:
        print("\nWarning: No database connection — articles not saved")

    run_timestamp = datetime.now().isoformat() + 'Z'
    run_date = datetime.now().strftime('%Y-%m-%d')

    print("\nGenerating balance.json entry...")
    balance_text = generate_balance(rejected_articles)
    if balance_text:
        try:
            with open('balance.json', 'r') as f:
                balance_entries = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            balance_entries = []
        balance_entries.insert(0, {'date': run_date, 'timestamp': run_timestamp, 'content': balance_text})
        with open('balance.json', 'w') as f:
            json.dump(balance_entries, f, indent=2)
        print("✓ balance.json updated")
    else:
        print("✗ balance.json skipped (no rejected articles or AI failure)")

    print("\nGenerating rallyingcry.json entry...")
    rallying_text = generate_rallying_cry(new_articles)
    if rallying_text:
        try:
            with open('rallyingcry.json', 'r') as f:
                rallying_entries = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            rallying_entries = []
        rallying_entries.insert(0, {'date': run_date, 'timestamp': run_timestamp, 'content': rallying_text})
        with open('rallyingcry.json', 'w') as f:
            json.dump(rallying_entries, f, indent=2)
        print("✓ rallyingcry.json updated")
    else:
        print("✗ rallyingcry.json skipped (no approved articles or AI failure)")

    print("\n" + "═" * 60)
    print(f"COMPLETE: {len(new_articles)} new articles added")
    print("═" * 60)

if __name__ == '__main__':
    scrape_news()
