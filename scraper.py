#!/usr/bin/env python3
"""
Rally News Scraper - Lenient version with external sources file
"""
import json
import random
import time
import os
from datetime import datetime
import requests
import feedparser
from bs4 import BeautifulSoup

HF_API_KEY = os.environ.get('HUGGINGFACE_API_KEY')
if not HF_API_KEY:
    raise ValueError("HUGGINGFACE_API_KEY not set")

HF_API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"

# Load sources from external file
with open('sources.json', 'r') as f:
    NEWS_SOURCES = json.load(f)

POSITIVE_KW = [
    'breakthrough', 'success', 'achieve', 'progress', 'innovation', 'improve',
    'advance', 'win', 'victory', 'solution', 'cure', 'recovery', 'growth',
    'agreement', 'cooperation', 'peace', 'deal', 'opens', 'launch', 'approve',
    'reform', 'protect', 'save', 'ceasefire', 'elect', 'union', 'strike ends'
]

NEGATIVE_KW = ['kill', 'death', 'dead', 'murder', 'bomb', 'destroy', 'collapse']

def quick_filter(title, summary):
    """Fast keyword filter"""
    text = (title + ' ' + summary).lower()
    if any(n in text for n in NEGATIVE_KW):
        return False
    return any(p in text for p in POSITIVE_KW)

def call_ai(prompt):
    """Call AI with fallback"""
    try:
        r = requests.post(
            HF_API_URL,
            headers={"Authorization": f"Bearer {HF_API_KEY}"},
            json={"inputs": prompt, "parameters": {"max_new_tokens": 100}},
            timeout=6
        )
        if r.status_code == 200:
            data = r.json()
            return data[0]["generated_text"] if isinstance(data, list) else data.get("generated_text", "")
    except:
        pass
    return None

def ai_check(title, summary):
    """Lenient AI check - accepts if shows ANY progress"""
    prompt = f"""Is this progress/improvement?

Accept if it shows: solutions, cooperation, new tech, reforms, deals, aid, protection, innovation, wins, breakthroughs, agreements, openings, launches.

Even if topic is heavy (war/strikes), accept if showing forward movement (ceasefire/deal/reform).

Title: {title}
Summary: {summary[:150]}

Answer: YES or NO"""

    result = call_ai(prompt)
    
    # Fallback: if AI fails/timeout, trust the keywords
    if result is None:
        print(f"       AI timeout, trusting keywords")
        return True
    
    return "YES" in result.upper()[:20]

def gen_cry(title, para):
    """Generate cry with fallback"""
    prompt = f"""One sentence (10 words) why this is positive:
{title}
{para[:150]}

Impact:"""
    
    result = call_ai(prompt)
    if result:
        return result.split('\n')[0].strip().strip('"\'')[:150]
    return title[:100]

def get_content(html):
    """Extract para + image"""
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

def fetch(url):
    """Fetch article"""
    try:
        r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code == 200:
            return get_content(r.text)
    except:
        pass
    return "", "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"

def scrape_source(name, url, target):
    """Scrape one source"""
    found = []
    print(f"\n📰 {name}...")
    
    try:
        feed = feedparser.parse(url)
        
        for entry in feed.entries[:25]:
            if len(found) >= min(2, target):
                break
            
            title = entry.get('title', '').strip()
            summary = entry.get('summary', entry.get('description', '')).strip()
            link = entry.get('link', '')
            
            if not title or not link:
                continue
            
            # Keywords
            if not quick_filter(title, summary):
                continue
            
            print(f"  📋 {title[:65]}")
            print(f"     Keywords ✓, checking AI...")
            
            # AI
            if not ai_check(title, summary):
                print(f"     ❌ No")
                continue
            
            print(f"     ✅ Yes!")
            
            para, img = fetch(link)
            if not para:
                para = summary[:500]
            
            cry = gen_cry(title, para)
            
            found.append({
                "title": title,
                "source": name,
                "url": link,
                "first_paragraph": para,
                "rallying_cry": cry,
                "image_url": img,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "category": "home"
            })
            
            print(f"     💚 {cry}")
            time.sleep(1.5)
        
    except Exception as e:
        print(f"  ⚠️  {e}")
    
    return found

def main():
    print("🌱 Rally Scraper")
    print(f"Target: 5 articles from {len(NEWS_SOURCES)} sources\n")
    
    # Random 10 sources
    sources = random.sample(list(NEWS_SOURCES.items()), min(10, len(NEWS_SOURCES)))
    print(f"Trying: {[n for n, _ in sources]}\n")
    
    all_new = []
    
    for name, url in sources:
        if len(all_new) >= 5:
            break
        found = scrape_source(name, url, 5 - len(all_new))
        all_new.extend(found)
        time.sleep(2)
    
    # Load existing (includes your hardcoded examples!)
    try:
        with open('news.json', 'r') as f:
            existing = json.load(f)
    except:
        existing = []
    
    # Add new to existing (keep examples!)
    seen = {a['url'] for a in existing}
    unique = [a for a in all_new if a['url'] not in seen]
    
    combined = unique + existing  # New articles first
    combined = combined[:200]
    
    with open('news.json', 'w') as f:
        json.dump(combined, f, indent=2)
    
    print(f"\n✅ Done!")
    print(f"📊 New: {len(unique)}")
    print(f"📚 Total: {len(combined)}")

if __name__ == "__main__":
    main()
