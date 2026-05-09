#!/usr/bin/env python3
"""
Rally News Scraper - Finds articles showing improvements to society
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

# Hugging Face API
HF_API_KEY = os.environ.get('HUGGINGFACE_API_KEY')
if not HF_API_KEY:
    raise ValueError("HUGGINGFACE_API_KEY environment variable not set")

HF_MODEL = "mistralai/Mistral-7B-Instruct-v0.3"
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"

# News sources (same as before)
NEWS_SOURCES = {
    "The New York Times": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "Washington Post": "https://feeds.washingtonpost.com/rss/world",
    "Reuters": "https://www.reutersagency.com/feed/",
    "AP News": "https://rsshub.app/apnews/topics/apf-topnews",
    "NPR": "https://feeds.npr.org/1001/rss.xml",
    "PBS News": "https://www.pbs.org/newshour/feeds/rss/headlines",
    "CNN": "http://rss.cnn.com/rss/cnn_topstories.rss",
    "NBC News": "https://feeds.nbcnews.com/nbcnews/public/news",
    "CBS News": "https://www.cbsnews.com/latest/rss/main",
    "USA Today": "http://rssfeeds.usatoday.com/usatoday-NewsTopStories",
    "The Atlantic": "https://www.theatlantic.com/feed/all/",
    "Bloomberg": "https://feeds.bloomberg.com/markets/news.rss",
    "BBC News": "http://feeds.bbci.co.uk/news/rss.xml",
    "The Guardian": "https://www.theguardian.com/world/rss",
    "Financial Times": "https://www.ft.com/?format=rss",
    "The Independent": "https://www.independent.co.uk/rss",
    "Sky News": "https://feeds.skynews.com/feeds/rss/home.xml",
    "CBC News": "https://www.cbc.ca/cmlink/rss-topstories",
    "The Globe and Mail": "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/canada/",
    "DW (Deutsche Welle)": "https://rss.dw.com/rdf/rss-en-all",
    "France 24": "https://www.france24.com/en/rss",
    "Al Jazeera English": "https://www.aljazeera.com/xml/rss/all.xml",
    "The Hindu": "https://www.thehindu.com/news/feeder/default.rss",
    "South China Morning Post": "https://www.scmp.com/rss/91/feed",
    "The Japan Times": "https://www.japantimes.co.jp/feed/",
    "ABC News (Australia)": "https://www.abc.net.au/news/feed/51120/rss.xml",
    "Wired": "https://www.wired.com/feed/rss",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "TechCrunch": "https://techcrunch.com/feed/",
    "New Scientist": "https://www.newscientist.com/subject/technology/feed/",
    "Le Monde": "https://www.lemonde.fr/en/rss/une.xml",
    "Los Angeles Times": "https://www.latimes.com/rss2.0.xml",
    "Wall Street Journal": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
}

def call_mistral(prompt, max_retries=2):
    """Call Mistral with retry logic"""
    for attempt in range(max_retries):
        try:
            response = requests.post(
                HF_API_URL,
                headers={"Authorization": f"Bearer {HF_API_KEY}"},
                json={
                    "inputs": prompt,
                    "parameters": {
                        "max_new_tokens": 200,
                        "temperature": 0.7,
                        "return_full_text": False
                    }
                },
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                text = data[0]["generated_text"] if isinstance(data, list) else data.get("generated_text", "")
                return text.strip()
            elif response.status_code == 503:
                print(f"  Model loading, waiting...")
                time.sleep(10)
                continue
            else:
                return None
                
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(3)
            continue
    
    return None

def is_positive_article(title, summary):
    """Use AI to determine if article shows improvement to society"""
    prompt = f"""Does this article focus on IMPROVEMENTS to society in these areas: Climate, Entertainment, AI/Science/Technology, Arts/Culture, Business/Finance, or Politics?

The article doesn't need to be purely positive, but must show clear progress, solutions, cooperation, or steps forward. Even heavy topics (war, crisis) can be positive if they show improvement.

POSITIVE examples:
- "Rent freeze approved in NYC" (housing affordability progress)
- "New subway opens in LA" (infrastructure improvement)
- "Union reaches deal after strike" (labor rights progress)
- "Scientists closer to HIV vaccine" (medical advancement)
- "3-day ceasefire agreed" (peace progress, even if temporary)
- "France sends ambassador to Algeria" (diplomatic improvement)

NEGATIVE examples:
- "Hurricane devastates coast" (disaster, no improvement)
- "Stocks plunge amid fears" (crisis, no solution)
- "Tensions rise between nations" (conflict escalating)

Title: {title}
Summary: {summary}

Answer ONLY "YES" if it shows improvement/progress, or "NO" if it's negative/neutral/no improvement."""

    result = call_mistral(prompt)
    if result and "YES" in result.upper()[:10]:
        return True
    return False

def generate_rallying_cry(title, first_paragraph):
    """Generate rallying cry explaining WHY article is positive"""
    prompt = f"""Write ONE sentence (under 15 words) explaining WHY this news is positive - what improvement or progress does it show?

Examples:
- "It seems like a rent freeze is on track to be announced in the US's biggest city."
- "A long-awaited public transit project is finally opening in Los Angeles."
- "Scientists are getting closer to a viable vaccine for HIV."
- "Thousands of Ukrainian and Russian soldiers will be spared fighting for their lives over the next three days."

Title: {title}
Article: {first_paragraph[:300]}

Rallying Cry:"""

    result = call_mistral(prompt)
    if result:
        cry = result.split('\n')[0].strip().strip('"\'')
        return cry[:150]
    return title[:100]

def extract_content(html):
    """Extract paragraph and image"""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Get image
        og_image = soup.find('meta', property='og:image')
        image = og_image['content'] if og_image and og_image.get('content') else "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"
        
        # Get first paragraph
        for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
            tag.decompose()
        
        for p in soup.find_all('p'):
            text = p.get_text().strip()
            if len(text) > 100:
                return text[:500], image
        
        return "", image
    except:
        return "", "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"

def scrape_article(url):
    """Fetch article content"""
    try:
        response = requests.get(url, timeout=8, headers={
            'User-Agent': 'Mozilla/5.0'
        })
        if response.status_code == 200:
            return extract_content(response.text)
        return "", "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"
    except:
        return "", "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"

def scrape_until_found(target_count=10):
    """Keep scraping until we find target_count positive articles"""
    print(f"🌱 Rally News Scraper - Finding {target_count} positive articles...")
    print(f"Time: {datetime.utcnow().isoformat()}\n")
    
    all_sources = list(NEWS_SOURCES.items())
    random.shuffle(all_sources)
    
    found_articles = []
    sources_tried = 0
    max_sources = 30  # Don't try forever
    
    for source_name, feed_url in all_sources:
        if len(found_articles) >= target_count:
            break
        
        if sources_tried >= max_sources:
            print(f"\n⚠️  Tried {max_sources} sources, stopping")
            break
        
        sources_tried += 1
        print(f"\n📰 [{len(found_articles)}/{target_count}] Scraping {source_name}...")
        
        try:
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:15]:  # Check up to 15 per source
                if len(found_articles) >= target_count:
                    break
                
                title = entry.get('title', '').strip()
                summary = entry.get('summary', entry.get('description', '')).strip()
                link = entry.get('link', '')
                
                if not title or not link:
                    continue
                
                print(f"  Checking: {title[:70]}...")
                
                # AI check
                if not is_positive_article(title, summary):
                    print(f"    ❌ Not showing improvement")
                    continue
                
                print(f"    ✅ Shows progress! Fetching details...")
                
                # Get full content
                first_para, image = scrape_article(link)
                if not first_para:
                    first_para = summary[:500]
                
                # Generate rallying cry
                rallying_cry = generate_rallying_cry(title, first_para)
                
                article = {
                    "title": title,
                    "source": source_name,
                    "url": link,
                    "first_paragraph": first_para,
                    "rallying_cry": rallying_cry,
                    "image_url": image,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "category": "home"
                }
                
                found_articles.append(article)
                print(f"    💚 Added ({len(found_articles)}/{target_count}): {rallying_cry}")
                
                time.sleep(2)
            
            time.sleep(3)  # Between sources
            
        except Exception as e:
            print(f"  Error: {e}")
            continue
    
    return found_articles

def main():
    # Scrape until we have 10 positive articles
    new_articles = scrape_until_found(target_count=10)
    
    # Load existing
    try:
        with open('news.json', 'r') as f:
            existing = json.load(f)
    except:
        existing = []
    
    # Combine
    seen_urls = {article['url'] for article in existing}
    unique_new = [a for a in new_articles if a['url'] not in seen_urls]
    
    combined = unique_new + existing
    combined = combined[:200]  # Keep 200 most recent
    
    # Save
    with open('news.json', 'w') as f:
        json.dump(combined, f, indent=2)
    
    print(f"\n✅ Scraping complete!")
    print(f"📊 Found {len(unique_new)} new positive articles")
    print(f"📚 Total in database: {len(combined)}")

if __name__ == "__main__":
    main()
