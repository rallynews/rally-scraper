#!/usr/bin/env python3
"""
Rally News Scraper - Modular version
Reads from: sources.json, criteria.txt, examples.json
"""
import json
import random
import time
import os
from datetime import datetime
import requests
import feedparser
from bs4 import BeautifulSoup

# Load Hugging Face API key
HF_API_KEY = os.environ.get('HUGGINGFACE_API_KEY')
if not HF_API_KEY:
    raise ValueError("HUGGINGFACE_API_KEY not set")

HF_API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"

# Load configuration files
print("📂 Loading configuration files...")

with open('sources.json', 'r') as f:
    NEWS_SOURCES = json.load(f)
    print(f"  ✓ Loaded {len(NEWS_SOURCES)} news sources")

with open('criteria.txt', 'r') as f:
    CRITERIA = f.read().strip()
    print(f"  ✓ Loaded filtering criteria ({len(CRITERIA)} chars)")

with open('examples.json', 'r') as f:
    EXAMPLES = json.load(f)
    print(f"  ✓ Loaded {len(EXAMPLES)} training examples\n")

# Simple keyword filters (fast pre-filter)
POSITIVE_KW = [
    'breakthrough', 'success', 'achieve', 'progress', 'innovation', 'improve',
    'advance', 'win', 'victory', 'solution', 'cure', 'recovery', 'growth',
    'agreement', 'cooperation', 'peace', 'deal', 'opens', 'launch', 'approve',
    'reform', 'protect', 'save', 'ceasefire', 'elect', 'union'
]

NEGATIVE_KW = ['kill', 'death', 'dead', 'murder', 'bomb', 'destroy', 'collapse']

def quick_filter(title, summary):
    """Fast keyword pre-filter before AI"""
    text = (title + ' ' + summary).lower()
    if any(n in text for n in NEGATIVE_KW):
        return False
    return any(p in text for p in POSITIVE_KW)

def call_ai(prompt):
    """Call AI with timeout"""
    try:
        r = requests.post(
            HF_API_URL,
            headers={"Authorization": f"Bearer {HF_API_KEY}"},
            json={"inputs": prompt, "parameters": {"max_new_tokens": 150, "temperature": 0.7}},
            timeout=8
        )
        if r.status_code == 200:
            data = r.json()
            return data[0]["generated_text"] if isinstance(data, list) else data.get("generated_text", "")
    except:
        pass
    return None

def build_ai_prompt(title, summary, task="check"):
    """Build AI prompt using criteria.txt and examples.json"""
    
    if task == "check":
        # Build prompt with criteria and examples
        examples_text = "\n\nHere are examples of positive stories:\n"
        for i, ex in enumerate(EXAMPLES[:10], 1):  # Use first 10 examples
            examples_text += f"{i}. \"{ex['title']}\" - {ex['why_positive']}\n"
        
        prompt = f"""{CRITERIA}

{examples_text}

Now evaluate this article:

Title: {title}
Summary: {summary[:200]}

Does this article show improvement or progress to society? Answer ONLY: YES or NO"""
        
        return prompt
    
    elif task == "rallying_cry":
        prompt = f"""Write ONE sentence (under 15 words) explaining WHY this news is positive - what improvement does it show?

Examples:
- "Housing affordability measure advances through political process"
- "Major public infrastructure project completed after years of work"
- "Scientific breakthrough in fighting antibiotic resistance"

Title: {title}
Article: {summary[:250]}

Positive impact:"""
        return prompt

def ai_check_positive(title, summary):
    """AI check using criteria.txt and examples.json"""
    prompt = build_ai_prompt(title, summary, task="check")
    result = call_ai(prompt)
    
    # Fallback: if AI times out, trust keywords
    if result is None:
        print(f"       ⏱️  AI timeout, trusting keywords")
        return True
    
    return "YES" in result.upper()[:20]

def generate_rallying_cry(title, summary):
    """Generate rallying cry explaining why it's positive"""
    prompt = build_ai_prompt(title, summary, task="rallying_cry")
    result = call_ai(prompt)
    
    if result:
        cry = result.split('\n')[0].strip().strip('"\'')
        return cry[:150]
    return title[:100]

def get_content(html):
    """Extract paragraph and image from HTML"""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Get image
        og = soup.find('meta', property='og:image')
        img = og['content'] if og and og.get('content') else "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"
        
        # Get first paragraph
        for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
            tag.decompose()
        
        for p in soup.find_all('p'):
            text = p.get_text().strip()
            if len(text) > 100 and not text.startswith(('By ', 'Published', 'Updated')):
                return text[:500], img
        
        return "", img
    except:
        return "", "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"

def fetch_article(url):
    """Fetch full article content"""
    try:
        r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code == 200:
            return get_content(r.text)
    except:
        pass
    return "", "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"

def scrape_source(source_name, feed_url, target_remaining):
    """Scrape one news source"""
    articles = []
    print(f"\n📰 Scraping {source_name}...")
    
    try:
        feed = feedparser.parse(feed_url)
        
        for entry in feed.entries[:20]:
            if len(articles) >= min(2, target_remaining):
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
            
            # Step 2: AI check using criteria.txt and examples.json
            if not ai_check_positive(title, summary):
                print(f"     ❌ Doesn't show improvement")
                continue
            
            print(f"     ✅ Shows progress! Getting details...")
            
            # Step 3: Fetch full article
            para, img = fetch_article(link)
            if not para:
                para = summary[:500]
            
            # Step 4: Generate rallying cry
            cry = generate_rallying_cry(title, para)
            
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
    print("🌱 Rally News Scraper")
    print(f"Target: 5 new articles\n")
    
    # Select random sources from sources.json
    sources = random.sample(list(NEWS_SOURCES.items()), min(10, len(NEWS_SOURCES)))
    print(f"Selected sources: {[n for n, _ in sources]}\n")
    
    all_articles = []
    
    for source_name, feed_url in sources:
        if len(all_articles) >= 5:
            break
        
        articles = scrape_source(source_name, feed_url, 5 - len(all_articles))
        all_articles.extend(articles)
        time.sleep(2)
    
    # Load existing news.json (includes hardcoded examples)
    try:
        with open('news.json', 'r') as f:
            existing = json.load(f)
    except:
        existing = []
    
    # Merge new articles with existing (preserve examples!)
    seen_urls = {article['url'] for article in existing}
    new_articles = [a for a in all_articles if a['url'] not in seen_urls]
    
    # Prepend new articles (newest first)
    combined = new_articles + existing
    combined = combined[:200]  # Keep 200 most recent
    
    # Save
    with open('news.json', 'w') as f:
        json.dump(combined, f, indent=2)
    
    print(f"\n✅ Scraping complete!")
    print(f"📊 New articles: {len(new_articles)}")
    print(f"📚 Total in database: {len(combined)}")

if __name__ == "__main__":
    main()
