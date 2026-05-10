#!/usr/bin/env python3
"""
Rally News Scraper - Completely Rebuilt
Only scrapes positive news from whitelisted sources within last 48 hours
"""

import requests
import json
import feedparser
from datetime import datetime, timedelta
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import time
import os

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')

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
    'Scientific American': 'http://rss.sciam.com/ScientificAmerican-Global',
    'Nature News': 'http://feeds.nature.com/nature/rss/current',
    'Science News': 'https://www.sciencenews.org/feed',
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

# Multi-model fallback (free models first, paid as fallback)
AI_MODELS = [
    'nvidia/llama-3.1-nemotron-70b-instruct',
    'poolside/laguna-70b-chat',
    'openai/gpt-4o-mini-2024-07-18',
    'minimax/minimax-01',
    'inclusionai/ring-flash-preview',
    'openai/o1-mini-2024-09-12',
    'google/gemini-2.0-flash-exp:free'  # Paid fallback
]

# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def is_recent(article_date):
    """Check if article is within last 48 hours"""
    if not article_date:
        return False
    
    try:
        if isinstance(article_date, str):
            # Try parsing various date formats
            for fmt in ['%a, %d %b %Y %H:%M:%S %z', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d']:
                try:
                    pub_date = datetime.strptime(article_date, fmt)
                    break
                except:
                    continue
            else:
                return False
        else:
            pub_date = datetime(*article_date[:6])
        
        # Make timezone-aware if naive
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=datetime.now().astimezone().tzinfo)
        
        cutoff = datetime.now(pub_date.tzinfo) - timedelta(hours=48)
        return pub_date > cutoff
    except:
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

def is_positive_news(title, summary):
    """Use AI to determine if article is genuinely positive news"""
    prompt = f"""Is this article about POSITIVE news (progress, achievements, solutions, help, innovation, recovery, cooperation)?

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
Summary: {article.get('summary', article.get('first_paragraph', ''))[:300]}

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
    if hasattr(entry, 'media_content') and entry.media_content:
        img = entry.media_content[0].get('url')
        if img and img not in used_images:
            return img
    
    # Try media thumbnail
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        img = entry.media_thumbnail[0].get('url')
        if img and img not in used_images:
            return img
    
    # Try enclosures
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for enc in entry.enclosures:
            if 'image' in enc.get('type', ''):
                img = enc.get('href')
                if img and img not in used_images:
                    return img
    
    # Fallback: fetch from page
    try:
        response = requests.get(entry.link, timeout=10, headers={
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
            img = urljoin(entry.link, img_tag['src'])
            if img not in used_images:
                return img
    except:
        pass
    
    return None

# ═══════════════════════════════════════════════════════════════
# MAIN SCRAPER
# ═══════════════════════════════════════════════════════════════

def scrape_news():
    """Main scraping function"""
    print("═" * 60)
    print("RALLY NEWS SCRAPER - Starting")
    print("═" * 60)
    
    # Load existing articles
    existing_articles = []
    used_images = set()
    
    try:
        with open('news.json', 'r') as f:
            existing_articles = json.load(f)
            # Remove rallying_cry field from existing articles
            for article in existing_articles:
                article.pop('rallying_cry', None)
            used_images = {a.get('image_url') for a in existing_articles if a.get('image_url')}
            print(f"Loaded {len(existing_articles)} existing articles")
    except FileNotFoundError:
        print("No existing articles found")
    
    new_articles = []
    
    # Scrape each feed
    for source_name, feed_url in RSS_FEEDS.items():
        if source_name not in WHITELISTED_SOURCES:
            continue
        
        print(f"\nScraping: {source_name}")
        
        try:
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:10]:  # Check 10 most recent
                # Check if recent (48 hours)
                pub_date = entry.get('published_parsed') or entry.get('updated_parsed')
                if not is_recent(pub_date):
                    continue
                
                title = entry.get('title', '').strip()
                summary = entry.get('summary', entry.get('description', '')).strip()
                url = entry.get('link', '').strip()
                
                if not all([title, url]):
                    continue
                
                # Skip if already exists
                if any(a['url'] == url for a in existing_articles):
                    continue
                
                # Check if positive news (AI filter)
                print(f"  Checking: {title[:60]}...")
                if not is_positive_news(title, summary):
                    print(f"    ✗ Not positive news")
                    continue
                
                print(f"    ✓ Positive news!")
                
                # Check for duplicate topics (compare with recent articles)
                combined_articles = new_articles + existing_articles
                if is_duplicate_topic(title, summary, combined_articles):
                    continue
                
                print(f"    ✓ Unique topic!")
                
                # Get unique image
                image_url = get_article_image(entry, used_images)
                if not image_url:
                    print(f"    ✗ No unique image found")
                    continue
                
                used_images.add(image_url)
                
                # Extract first paragraph
                first_paragraph = extract_first_paragraph(url)
                if not first_paragraph:
                    first_paragraph = summary[:500]
                
                # Categorize
                category = categorize_article(title, summary)
                print(f"    ✓ Categorized as: {category}")
                
                # Create article object (NO rallying_cry field)
                article = {
                    'title': title,
                    'source': source_name,
                    'url': url,
                    'first_paragraph': first_paragraph,
                    'summary': summary[:300] if summary else first_paragraph[:300],
                    'image_url': image_url,
                    'timestamp': datetime.now().isoformat() + 'Z',
                    'category': category
                }
                
                new_articles.append(article)
                print(f"    ✓ Added to queue")
                
                # Rate limiting
                time.sleep(2)
        
        except Exception as e:
            print(f"  ✗ Error scraping {source_name}: {str(e)}")
            continue
    
    # Merge with existing articles
    all_articles = new_articles + existing_articles
    
    # Remove duplicates by URL
    seen_urls = set()
    unique_articles = []
    for article in all_articles:
        if article['url'] not in seen_urls:
            seen_urls.add(article['url'])
            unique_articles.append(article)
    
    # Sort by timestamp (newest first)
    unique_articles.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    # Keep only last 100 articles
    final_articles = unique_articles[:100]
    
    # Save
    with open('news.json', 'w') as f:
        json.dump(final_articles, f, indent=2)
    
    print("\n" + "═" * 60)
    print(f"COMPLETE: {len(new_articles)} new articles added")
    print(f"Total articles: {len(final_articles)}")
    print("═" * 60)

if __name__ == '__main__':
    scrape_news()
