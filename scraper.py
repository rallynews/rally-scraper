#!/usr/bin/env python3
"""
Rally News Scraper - Completely Rebuilt
Only scrapes positive news from whitelisted sources within last 48 hours
"""

import re
import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import time
import os

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
NEWS_API_URL = os.environ.get('NEWS_API_URL')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY')

# Derive sibling endpoints from NEWS_API_URL
_api_base = NEWS_API_URL.rsplit('/', 1)[0] if NEWS_API_URL else None
BALANCE_API_URL     = f"{_api_base}/balance.php"     if _api_base else None
RALLYING_API_URL    = f"{_api_base}/rallying-cry.php" if _api_base else None

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
    'Grist', 'Science', 'New Scientist',
    # Added 2026-07: broader global coverage
    'Smithsonian Magazine', 'The Narwhal', 'Euronews',
    'Kyiv Independent', 'The Moscow Times', 'El País (English)',
    'Dawn', 'Rappler', 'Daily Maverick', 'Africanews',
    'ScienceAlert', 'Aeon'
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
    # Added 2026-07: broader global coverage
    'Smithsonian Magazine': 'https://www.smithsonianmag.com/rss/latest_articles/',
    'The Narwhal': 'https://thenarwhal.ca/feed/',
    'Euronews': 'https://www.euronews.com/rss',
    'Kyiv Independent': 'https://kyivindependent.com/feed/rss',
    'The Moscow Times': 'https://www.themoscowtimes.com/rss/news',
    'El País (English)': 'https://feeds.elpais.com/mrss-s/pages/ep/site/english.elpais.com/portada',
    'Dawn': 'https://www.dawn.com/feeds/home',
    'Rappler': 'https://www.rappler.com/feed/',
    'Daily Maverick': 'https://www.dailymaverick.co.za/rss/',
    'Africanews': 'https://www.africanews.com/feed/rss',
    'ScienceAlert': 'https://www.sciencealert.com/feed',
    'Aeon': 'https://aeon.co/feed.rss'
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

# Continent of each whitelisted source's newsroom/origin.
# Used to guarantee every run surfaces at least one story per continent.
SOURCE_CONTINENTS = {
    # North America
    'NPR': 'North America', 'The New York Times': 'North America',
    'The Washington Post': 'North America', 'The Atlantic': 'North America',
    'Scientific American': 'North America', 'Science News': 'North America',
    'Wired': 'North America', 'TechCrunch': 'North America',
    'Ars Technica': 'North America', 'MIT Technology Review': 'North America',
    'The Wall Street Journal': 'North America', 'Bloomberg': 'North America',
    'CNBC': 'North America', 'Los Angeles Times': 'North America',
    'The Globe and Mail': 'North America', 'Grist': 'North America',
    'Science': 'North America', 'Smithsonian Magazine': 'North America',
    'The Narwhal': 'North America',
    # Europe
    'BBC News': 'Europe', 'The Guardian': 'Europe', 'Reuters': 'Europe',
    'Nature News': 'Europe', 'Le Monde': 'Europe', 'DW (Deutsche Welle)': 'Europe',
    'The Telegraph': 'Europe', 'New Scientist': 'Europe', 'Euronews': 'Europe',
    'Kyiv Independent': 'Europe', 'The Moscow Times': 'Europe',
    'El País (English)': 'Europe',
    # Asia
    'Al Jazeera': 'Asia', 'The Japan Times': 'Asia', 'The Straits Times': 'Asia',
    'Dawn': 'Asia', 'Rappler': 'Asia',
    # Africa
    'Daily Maverick': 'Africa', 'Africanews': 'Africa',
    # Oceania
    'The Sydney Morning Herald': 'Oceania', 'ScienceAlert': 'Oceania',
    'Aeon': 'Oceania',
}

# Selection limits applied to each scraper run
MIN_NEW_ARTICLES = 15      # target new positive stories per run
MAX_PER_CATEGORY = 2       # no more than this many stories in any one category

# Editorial metadata options (AI will assign these to every new article).
# Decided by Mistral (mistral-small-3.2-24b-instruct is first in AI_MODELS);
# other models only step in as a fallback if Mistral is unavailable.
VALID_WRITING_STYLES = ['Formal', 'Casual', 'Scientific', 'Investigative', 'Funny', 'Thoughtful']
VALID_COMPLEXITY = ['Simple', 'Moderate', 'Complex']

# Free Gemini first, then paid o1-mini, then stable fallbacks
AI_MODELS = [
    'mistralai/mistral-small-3.2-24b-instruct',  # cheap, Europe-based, hits first
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

        media_content = [
            {'url': el.get('url'), 'width': int(el.get('width', 0) or 0)}
            for el in item
            if el.tag == f'{{{MEDIA_NS}}}content' and el.get('url')
        ]
        media_content.sort(key=lambda x: x['width'], reverse=True)
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
                print(f"✗ Model {model} failed: {response.status_code} — {response.text[:200]}")
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
                print(f"✗ Model {model} failed: {response.status_code} — {response.text[:200]}")
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
        f"- {a['title']} | {a['url']}"
        for a in approved_articles[:20]
    )

    prompt = f"""You have the following positive news headlines. Write a single, upbeat, conversational one-sentence summary of today's good news, mentioning 2–4 specific stories naturally.

Style examples (do not copy these, they are just to show the tone and structure):
- "A new hydropower startup got funded for 2.5 million, Warsaw government recognizes gay marriage, and six new delicious recipes to try this year."
- "A new, reform-minded Prime Minister promises change in India, and global child hunger drops to its lowest ever."
- "Remembering the life and works of Alan Rickman, and a new airport opens its doors in Rio."

Headlines (title | url):
{articles_text}

Respond with valid JSON only, in this exact format:
{{
  "content": "Your single sentence here.",
  "stories": [
    {{"title": "Exact title of a story you mentioned", "url": "its url from the list above"}},
    {{"title": "Another title", "url": "its url"}}
  ]
}}

The "stories" array must contain only the 2–4 articles you actually referenced in your sentence, with titles and URLs copied exactly from the list above. Be specific, conversational, and uplifting. Do not use quotation marks around the sentence."""

    result = call_ai_long(prompt, max_tokens=400, timeout=30)
    if not result:
        return None
    # Some models wrap JSON in markdown code fences — strip them before parsing
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.strip())
    try:
        data = json.loads(cleaned)
        stories = [s for s in data.get('stories', []) if isinstance(s, dict) and 'title' in s]
        return {'content': data.get('content', ''), 'stories': stories}
    except (json.JSONDecodeError, AttributeError):
        # AI returned plain text instead of JSON — use it as content directly
        if cleaned.startswith('{') or cleaned.startswith('['):
            return None  # Still malformed JSON, skip rather than surface garbage
        return {'content': cleaned, 'stories': []}

def generate_rallying_cry_rss(entry):
    """Write rallyingcries.rss containing only the single most recent rallying cry."""
    site_url = ''
    if RALLYING_API_URL:
        parts = RALLYING_API_URL.split('/api/')
        site_url = parts[0] if len(parts) > 1 else RALLYING_API_URL

    def to_rfc2822(ts_str):
        try:
            dt = datetime.strptime(ts_str.rstrip('Z'), '%Y-%m-%dT%H:%M:%S')
            return dt.strftime('%a, %d %b %Y %H:%M:%S +0000')
        except Exception:
            return datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S +0000')

    def format_title_date(date_str):
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return dt.strftime('%B %-d, %Y')
        except Exception:
            return date_str

    def xml_escape(text):
        return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))

    now_rfc = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S +0000')
    title = f"Rallying Cry – {format_title_date(entry.get('date', ''))}"

    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        '  <channel>\n'
        '    <title>Rallying Cries – Rally News</title>\n'
        f'    <link>{xml_escape(site_url)}</link>\n'
        '    <description>The most recent uplifting one-sentence summary of the day\'s positive news.</description>\n'
        '    <language>en-us</language>\n'
        f'    <lastBuildDate>{now_rfc}</lastBuildDate>\n'
        '    <item>\n'
        f'      <title>{xml_escape(title)}</title>\n'
        f'      <description>{xml_escape(entry.get("content", ""))}</description>\n'
        f'      <pubDate>{to_rfc2822(entry.get("timestamp", ""))}</pubDate>\n'
        f'      <guid isPermaLink="false">rallying-cry-{xml_escape(entry.get("timestamp", entry.get("date", "")))}</guid>\n'
        '    </item>\n'
        '  </channel>\n'
        '</rss>\n'
    )

    with open('rallyingcries.rss', 'w', encoding='utf-8') as f:
        f.write(rss)
    print("✓ rallyingcries.rss updated")

def is_positive_news(title, summary):
    """Use AI to determine if article is genuinely positive news"""
    prompt = f"""Is this article about POSITIVE news (progress, achievements, solutions, help, innovation, recovery, cooperation)? Positive news is not controversial, and is actively showing progress. It is also not focused on the acquisition of wealth by large corporations or corporations making deals with each other which do not benefit humanity [...]

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
- NO if it's primarily about big companies or corporate interests making deals with each other. 
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

def enrich_article_metadata(title, summary, content):
    """Use Mistral (acting as an expert content editor) to add editorial metadata
    to a new article, based only on its headline, summary, and first paragraph."""
    prompt = f"""You are an expert content editor for a positive news outlet. Read the headline, summary, and first paragraph below and decide five pieces of metadata about it. Base every judgment strictly on the text given — do not guess, assume, or invent anything that isn't actually stated or clearly implied.

Headline: {title}
Summary: {summary}
First paragraph: {content}

Decide:
1. writing_style — the tone/register of THIS reporting (not the underlying event). Pick 1, or at most 2 if the piece genuinely blends two. Choose only from: Formal, Casual, Scientific, Investigative, Funny, Thoughtful.
2. complexity — how demanding the writing is to read. Choose exactly one: Simple, Moderate, Complex.
3. topics — the general subject area(s) this story is about, as short lowercase phrases (e.g. "sports", "space", "movies", "tv", "books", "politics", "health", "wildlife"). List only topics that are clearly central to the story, at most 4.
4. countries — real-world countries that are directly affected by or are the setting of this story. Use full country names. Leave empty if no specific country is identifiable.
5. people — real, specific named individuals (full names as given) who are actually mentioned in this text. Do not include organizations, generic titles, or fictional characters. Leave empty if none are named.

Respond with valid JSON only, in this exact format and nothing else:
{{
  "writing_style": ["Thoughtful"],
  "complexity": "Moderate",
  "topics": ["example topic"],
  "countries": ["Example Country"],
  "people": ["Example Name"]
}}"""

    result = call_ai_long(prompt, max_tokens=300, timeout=20)
    defaults = {'writing_style': [], 'complexity': 'Moderate', 'topics': [], 'countries': [], 'people': []}
    if not result:
        return defaults

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.strip())
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, AttributeError):
        return defaults

    styles = data.get('writing_style', [])
    if isinstance(styles, str):
        styles = [styles]
    styles = [s for s in styles if s in VALID_WRITING_STYLES][:2]

    complexity = data.get('complexity', '')
    if complexity not in VALID_COMPLEXITY:
        complexity = 'Moderate'

    def clean_list(key):
        values = data.get(key, [])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return []
        return [str(v).strip() for v in values if isinstance(v, (str, int, float)) and str(v).strip()]

    return {
        'writing_style': styles,
        'complexity': complexity,
        'topics': clean_list('topics'),
        'countries': clean_list('countries'),
        'people': clean_list('people'),
    }

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

def upscale_image_url(url):
    """Try to swap known low-res CDN parameters for higher-res equivalents.
    Falls back to the original URL if anything goes wrong."""
    if not url:
        return url
    try:
        # BBC: ichef.bbci.co.uk/ace/standard/{n}/ — replace any width with 1024
        url = re.sub(
            r'(ichef\.bbci\.co\.uk/ace/standard/)\d+/',
            r'\g<1>1024/',
            url
        )
        # SMH/FFX: $width_NNN and $height_NNN URL params
        url = re.sub(r'\$width_\d+', '$width_1200', url)
        url = re.sub(r'\$height_\d+', '$height_675', url)
        return url
    except Exception:
        return url


def get_article_image(entry, used_images):
    """Extract unique image URL from article"""
    # Try media content
    if entry.get('media_content'):
        img = entry['media_content'][0].get('url')
        if img and img not in used_images:
            return upscale_image_url(img)

    # Try media thumbnail
    if entry.get('media_thumbnail'):
        img = entry['media_thumbnail'][0].get('url')
        if img and img not in used_images:
            return upscale_image_url(img)

    # Try enclosures
    if entry.get('enclosures'):
        for enc in entry['enclosures']:
            if 'image' in enc.get('type', ''):
                img = enc.get('href')
                if img and img not in used_images:
                    return upscale_image_url(img)

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
                return upscale_image_url(img)

        # Try first img tag
        img_tag = soup.find('img', src=True)
        if img_tag:
            img = urljoin(article_url, img_tag['src'])
            if img not in used_images:
                return upscale_image_url(img)
    except:
        pass

    return None

# ═══════════════════════════════════════════════════════════════
# DATABASE API
# ═══════════════════════════════════════════════════════════════

def api_get(params=None):
    """GET articles from the PHP API. Returns list or None on failure."""
    try:
        resp = requests.get(NEWS_API_URL, params=params, timeout=30)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"Warning: API GET failed ({type(e).__name__}): {e}")
    return None

def api_post(articles):
    """POST articles to the PHP API. Returns number inserted or 0 on failure."""
    try:
        resp = requests.post(
            NEWS_API_URL,
            json=articles,
            headers={'X-API-Key': NEWS_API_KEY, 'Content-Type': 'application/json'},
            timeout=60,
        )
        if resp.ok:
            return resp.json().get('inserted', 0)
        print(f"Warning: API POST returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"Warning: API POST failed ({type(e).__name__}): {e}")
    return 0

def api_post_entry(url, entry):
    """POST a single {date, timestamp, content} entry to a sidecar endpoint."""
    if not url or not NEWS_API_KEY:
        return False
    try:
        resp = requests.post(
            url,
            json=entry,
            headers={'X-API-Key': NEWS_API_KEY, 'Content-Type': 'application/json'},
            timeout=15,
        )
        return resp.ok
    except Exception as e:
        print(f"Warning: POST to {url} failed ({type(e).__name__}): {e}")
        return False

def api_get_entries(url, limit=50):
    """GET entries from a sidecar endpoint. Returns list or None on failure."""
    if not url:
        return None
    try:
        resp = requests.get(url, params={'limit': limit}, timeout=15)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"Warning: GET {url} failed ({type(e).__name__}): {e}")
    return None

def migrate_json_entries_via_api(json_path, endpoint_url):
    """One-time migration: POST all entries from a JSON file if the table is empty."""
    if not endpoint_url or not NEWS_API_KEY:
        return
    try:
        resp = requests.get(endpoint_url, params={'limit': 1}, timeout=15)
        if not resp.ok or len(resp.json()) > 0:
            return
    except Exception:
        return
    try:
        with open(json_path, 'r') as f:
            entries = json.load(f)
    except FileNotFoundError:
        return
    if not entries:
        return
    print(f"First run: migrating {len(entries)} entries from {json_path}...")
    try:
        resp = requests.post(
            endpoint_url,
            json=entries,
            headers={'X-API-Key': NEWS_API_KEY, 'Content-Type': 'application/json'},
            timeout=30,
        )
        if resp.ok:
            print(f"✓ Migrated {json_path}")
        else:
            print(f"✗ Migration of {json_path} failed: {resp.status_code}")
    except Exception as e:
        print(f"✗ Migration of {json_path} error: {e}")

def migrate_from_json_via_api():
    """One-time migration: POST all news.json articles to API if the DB is empty."""
    existing = api_get({'limit': 1})
    if existing is None or len(existing) > 0:
        return

    try:
        with open('news.json', 'r') as f:
            articles = json.load(f)
    except FileNotFoundError:
        return

    if not articles:
        return

    print(f"First run: migrating {len(articles)} articles to database...")
    inserted = api_post(articles)
    print(f"✓ Migration complete: {inserted} articles inserted")

# ═══════════════════════════════════════════════════════════════
# MAIN SCRAPER
# ═══════════════════════════════════════════════════════════════

def scrape_news():
    """Main scraping function"""
    print("═" * 60)
    print("RALLY NEWS SCRAPER - Starting")
    print("═" * 60)

    SCRAPE_TIMEOUT = 45 * 60   # 45 minutes max per run
    BATCH_SIZE = 10             # feed entries examined per pass

    # Continents we expect to cover — every continent that has a live feed.
    required_continents = {
        SOURCE_CONTINENTS[s] for s in RSS_FEEDS
        if s in WHITELISTED_SOURCES and s in SOURCE_CONTINENTS
    }

    start_time = time.time()

    # Load existing articles for deduplication
    existing_articles = []
    used_images = set()
    existing_urls = set()
    api_available = bool(NEWS_API_URL and NEWS_API_KEY)
    print(f"NEWS_API_URL set: {bool(NEWS_API_URL)}")
    print(f"NEWS_API_KEY set: {bool(NEWS_API_KEY)}")

    if api_available:
        migrate_from_json_via_api()
        migrate_json_entries_via_api('balance.json', BALANCE_API_URL)
        migrate_json_entries_via_api('rallyingcry.json', RALLYING_API_URL)
        fetched = api_get({'limit': 200})
        print(f"API GET result: {type(fetched).__name__}, length={len(fetched) if fetched is not None else 'N/A'}")
        if fetched is not None:
            existing_articles = fetched
            used_images = {a.get('image_url') for a in existing_articles if a.get('image_url')}
            existing_urls = {a['url'] for a in existing_articles}
            print(f"Loaded {len(existing_articles)} existing articles from database")
        else:
            api_available = False

    if not api_available:
        try:
            with open('news.json', 'r') as f:
                existing_articles = json.load(f)
                for article in existing_articles:
                    article.pop('rallying_cry', None)
                used_images = {a.get('image_url') for a in existing_articles if a.get('image_url')}
                existing_urls = {a['url'] for a in existing_articles}
                print(f"Falling back to news.json: {len(existing_articles)} existing articles loaded")
        except FileNotFoundError:
            print("No news.json found — starting fresh")

    new_articles = []
    rejected_articles = []   # negative/neutral articles collected for balance.json
    checked_urls = set()     # URLs already evaluated this run (across all passes)
    pass_num = 0

    while True:
        elapsed = time.time() - start_time

        if elapsed >= SCRAPE_TIMEOUT:
            print(f"\nTimeout reached after {pass_num} passes ({elapsed/60:.1f} min)")
            break

        covered_continents = {
            SOURCE_CONTINENTS.get(a['source']) for a in new_articles
        }
        covered_continents.discard(None)
        missing_continents = required_continents - covered_continents

        if len(new_articles) >= MIN_NEW_ARTICLES and not missing_continents:
            print(f"\nTarget reached: {len(new_articles)} new articles found, "
                  f"all {len(required_continents)} continents covered")
            break

        start_idx = pass_num * BATCH_SIZE
        end_idx = start_idx + BATCH_SIZE

        print(f"\n{'─' * 60}")
        print(f"Pass {pass_num + 1}: checking feed entries {start_idx + 1}–{end_idx}")
        print(f"Articles found so far: {len(new_articles)}/{MIN_NEW_ARTICLES}")
        if missing_continents:
            print(f"Continents still needed: {', '.join(sorted(missing_continents))}")
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

                    # Once the target is met, only keep scraping to fill in
                    # continents we don't yet have a story from.
                    continent = SOURCE_CONTINENTS.get(source_name)
                    if len(new_articles) >= MIN_NEW_ARTICLES:
                        covered_now = {SOURCE_CONTINENTS.get(a['source']) for a in new_articles}
                        if continent is None or continent in covered_now:
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

                    category = categorize_article(title, summary)
                    print(f"    ✓ Categorized as: {category}")

                    # Cap: no more than MAX_PER_CATEGORY stories in any one category
                    category_count = sum(1 for a in new_articles if a['category'] == category)
                    if category_count >= MAX_PER_CATEGORY:
                        print(f"    ✗ Already have {MAX_PER_CATEGORY} '{category}' stories this run")
                        continue

                    image_url = get_article_image(entry, used_images)
                    if not image_url:
                        print(f"    ✗ No unique image found")
                        continue

                    used_images.add(image_url)

                    content = extract_first_paragraph(url)
                    if not content:
                        content = summary[:500]

                    metadata = enrich_article_metadata(title, summary, content)
                    print(f"    ✓ Metadata: style={metadata['writing_style']}, "
                          f"complexity={metadata['complexity']}, topics={metadata['topics']}")

                    article = {
                        'title': title,
                        'source': source_name,
                        'url': url,
                        'content': content,
                        'summary': summary[:300] if summary else content[:300],
                        'image_url': image_url,
                        'timestamp': datetime.now().isoformat() + 'Z',
                        'category': category,
                        'writing_style': metadata['writing_style'],
                        'complexity': metadata['complexity'],
                        'topics': metadata['topics'],
                        'countries': metadata['countries'],
                        'people': metadata['people'],
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

    # Save new articles to database via API (best-effort)
    if api_available and new_articles:
        saved = api_post(new_articles)
        print(f"\nSaved {saved} new articles to database")

    run_timestamp = datetime.now().isoformat() + 'Z'
    run_date = datetime.now().strftime('%Y-%m-%d')

    print("\nGenerating balance entry...")
    balance_result = generate_balance(rejected_articles)
    if balance_result:
        entry = {
            'date': run_date,
            'timestamp': run_timestamp,
            'content': balance_result,
        }
        if api_available:
            ok = api_post_entry(BALANCE_API_URL, entry)
            print("✓ balance saved to database" if ok else "✗ balance database write failed")
    else:
        print("✗ balance skipped (no rejected articles or AI failure)")

    print("\nGenerating rallying cry entry...")
    rallying_result = generate_rallying_cry(new_articles)
    if rallying_result:
        # Same pattern: clean JSON in content so stories survive the PHP backend.
        entry = {
            'date': run_date,
            'timestamp': run_timestamp,
            'content': json.dumps({'content': rallying_result['content'], 'stories': rallying_result['stories']}),
        }
        if api_available:
            ok = api_post_entry(RALLYING_API_URL, entry)
            print("✓ rallying cry saved to database" if ok else "✗ rallying cry database write failed")
        generate_rallying_cry_rss({'date': run_date, 'timestamp': run_timestamp, 'content': rallying_result['content']})
    else:
        print("✗ rallying cry skipped (no approved articles or AI failure)")

    print("\n" + "═" * 60)
    print(f"COMPLETE: {len(new_articles)} new articles added")
    print("═" * 60)

if __name__ == '__main__':
    scrape_news()
