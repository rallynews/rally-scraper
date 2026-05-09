#!/usr/bin/env python3
"""
Rally News Scraper - Fast version with keyword pre-filtering
"""
import json
import random
import time
import os
from datetime import datetime
import requests
import feedparser
from bs4 import BeautifulSoup
from urllib.parse import urljoin

HF_API_KEY = os.environ.get('HUGGINGFACE_API_KEY')
if not HF_API_KEY:
    raise ValueError("HUGGINGFACE_API_KEY environment variable not set")

HF_MODEL = "mistralai/Mistral-7B-Instruct-v0.3"
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"

NEWS_SOURCES = {
    "The New York Times": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "Washington Post": "https://feeds.washingtonpost.com/rss/world",
    "Reuters": "https://www.reutersagency.com/feed/",
    "NPR": "https://feeds.npr.org/1001/rss.xml",
    "CNN": "http://rss.cnn.com/rss/cnn_topstories.rss",
    "BBC News": "http://feeds.bbci.co.uk/news/rss.xml",
    "The Guardian": "https://www.theguardian.com/world/rss",
    "Al Jazeera English": "https://www.aljazeera.com/xml/rss/all.xml",
    "Wired": "https://www.wired.com/feed/rss",
    "Los Angeles Times": "https://www.latimes.com/rss2.0.xml",
    "Le Monde": "https://www.lemonde.fr/en/rss/une.xml",
    "DW (Deutsche Welle)": "https://rss.dw.com/rdf/rss-en-all",
}

# Fast keyword filters
POSITIVE_KEYWORDS = [
    'breakthrough', 'success', 'achieve', 'progress', 'innovation', 'improve',
    'advance', 'win', 'victory', 'solution', 'cure', 'recovery', 'growth',
    'agreement', 'cooperation', 'peace', 'deal', 'opens', 'launch', 'new',
    'first', 'approve', 'pass', 'vote', 'elect', 'reform', 'protect', 'save'
]

NEGATIVE_KEYWORDS = [
    'kill', 'death', 'dead', 'die', 'murder', 'terror', 'attack', 'bomb',
    'crash', 'disaster', 'devastate', 'destroy', 'collapse', 'plunge', 'crisis'
]

def quick_filter(title, summary):
    """Fast keyword-based filter before AI"""
    text = (title + ' ' + summary).lower()
    
    # Reject if has negative keywords
    if any(neg in text for neg in NEGATIVE_KEYWORDS):
        return False
    
    # Accept if has positive keywords
    if any(pos in text for pos in POSITIVE_KEYWORDS):
        return True
    
    # Otherwise reject (be conservative)
    return False

def call_ai(prompt, timeout=8):
    """Quick AI call with short timeout"""
    try:
        response = requests.post(
            HF_API_URL,
            headers={"Authorization": f"Bearer {HF_API_KEY}"},
            json={
                "inputs": prompt,
                "parameters": {"max_new_tokens": 150, "temperature": 0.7}
            },
            timeout=timeout
        )
        
        if response.status_code == 200:
            data = response.json()
            return data[0]["generated_text"] if isinstance(data, list) else data.get("generated_text", "")
        return None
    except:
        return None

def ai_check_positive(title, summary):
    """AI check if shows improvement"""
    prompt = f"""Does this show IMPROVEMENT/PROGRESS in: Climate, Entertainment, AI/Tech, Arts/Culture, Business/Finance, or Politics?

Even heavy topics (war, strikes) are YES if they show progress (ceasefire, deal reached).

Title: {title}
Summary: {summary[:200]}

Answer: YES or NO"""

    result = call_ai(prompt)
    return result and "YES" in result.upper()[:15]

def generate_cry(title, para):
    """Generate rallying cry"""
    prompt = f"""One sentence (under 15 words) explaining the positive impact:

Title: {title}
Text: {para[:200]}

Rallying cry:"""
    
    result = call_ai(prompt, timeout=10)
    if result:
        return result.split('\n')[0].strip().strip('"\'')[:150]
    return title[:100]

def get_content(html):
    """Extract paragraph and image"""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        og = soup.find('meta', property='og:image')
        img = og['content'] if og and og.get('content') else "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"
        
        for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
            tag.decompose()
        
        for p in soup.find_all('p'):
            text = p.get_text().strip()
            if len(text) > 100:
                return text[:500], img
        return "", img
    except:
        return "", "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"

def fetch_article(url):
    """Get article content"""
    try:
        r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code == 200:
            return get_content(r.text)
    except:
        pass
    return "", "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"

def scrape_source(source_name, feed_url, target_remaining):
    """Scrape one source"""
    articles = []
    print(f"\n📰 Scraping {source_name}...")
    
    try:
        feed = feedparser.parse(feed_url)
        
        for entry in feed.entries[:20]:
            if len(articles) >= min(3, target_remaining):  # Max 3 per source
                break
            
            title = entry.get('title', '').strip()
            summary = entry.get('summary', entry.get('description', '')).strip()
            link = entry.get('link', '')
            
            if not title or not link:
                continue
            
            # Step 1: Quick keyword filter
            if not quick_filter(title, summary):
                continue
            
            print(f"  📋 {title[:70]}")
            print(f"     Keywords look good, checking with AI...")
            
            # Step 2: AI verification
            if not ai_check_positive(title, summary):
                print(f"     ❌ AI says no improvement")
                continue
            
            print(f"     ✅ AI confirmed! Getting details...")
            
            # Step 3: Get full content
            para, img = fetch_article(link)
            if not para:
                para = summary[:500]
            
            # Step 4: Generate rallying cry
            cry = generate_cry(title, para)
            
            article = {
                "title": title,
                "source": source_name,
                "url": link,
                "first_paragraph": para,
                "rallying_cry": cry,
                "image_url": img,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "category": "home"
            }
            
            articles.append(article)
            print(f"     💚 Added: {cry}")
            time.sleep(2)
        
    except Exception as e:
        print(f"  ⚠️  Error: {e}")
    
    return articles

def main():
    print("🌱 Rally News Scraper (Fast Mode)")
    print(f"Target: 5 new articles\n")
    
    # Try 8 random sources
    sources = random.sample(list(NEWS_SOURCES.items()), min(8, len(NEWS_SOURCES)))
    print(f"Sources: {[n for n, _ in sources]}\n")
    
    all_articles = []
    
    for source_name, feed_url in sources:
        if len(all_articles) >= 5:
            break
        
        articles = scrape_source(source_name, feed_url, 5 - len(all_articles))
        all_articles.extend(articles)
        time.sleep(2)
    
    # Load existing
    try:
        with open('news.json', 'r') as f:
            existing = json.load(f)
    except:
        existing = []
    
    # Merge
    seen = {a['url'] for a in existing}
    new = [a for a in all_articles if a['url'] not in seen]
    
    combined = new + existing
    combined = combined[:200]
    
    # Save
    with open('news.json', 'w') as f:
        json.dump(combined, f, indent=2)
    
    print(f"\n✅ Complete!")
    print(f"📊 New articles: {len(new)}")
    print(f"📚 Total: {len(combined)}")

if __name__ == "__main__":
    main()
