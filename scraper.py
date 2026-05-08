#!/usr/bin/env python3
"""
Rally News Scraper - Fetches positive news from curated sources
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

# Hugging Face API - reads from environment variable
HF_API_KEY = os.environ.get('HUGGINGFACE_API_KEY')
if not HF_API_KEY:
    raise ValueError("HUGGINGFACE_API_KEY environment variable not set")
    
HF_MODEL = "mistralai/Mistral-7B-Instruct-v0.3"
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"

# News source whitelist with RSS feeds
NEWS_SOURCES = {
    # US Major
    "The New York Times": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "Washington Post": "https://feeds.washingtonpost.com/rss/world",
    "Reuters": "https://www.reutersagency.com/feed/",
    "AP News": "https://rsshub.app/apnews/topics/apf-topnews",
    "NPR": "https://feeds.npr.org/1001/rss.xml",
    "PBS News": "https://www.pbs.org/newshour/feeds/rss/headlines",
    "CNN": "http://rss.cnn.com/rss/cnn_topstories.rss",
    "NBC News": "https://feeds.nbcnews.com/nbcnews/public/news",
    "CBS News": "https://www.cbsnews.com/latest/rss/main",
    "ABC News": "https://abcnews.go.com/abcnews/topstories",
    "USA Today": "http://rssfeeds.usatoday.com/usatoday-NewsTopStories",
    "The Atlantic": "https://www.theatlantic.com/feed/all/",
    "Bloomberg": "https://feeds.bloomberg.com/markets/news.rss",
    
    # UK/Ireland
    "BBC News": "http://feeds.bbci.co.uk/news/rss.xml",
    "The Guardian": "https://www.theguardian.com/world/rss",
    "Financial Times": "https://www.ft.com/?format=rss",
    "The Independent": "https://www.independent.co.uk/rss",
    "The Economist": "https://www.economist.com/rss",
    "Sky News": "https://feeds.skynews.com/feeds/rss/home.xml",
    "The Irish Times": "https://www.irishtimes.com/cmlink/news-1.1319192",
    
    # Canada
    "CBC News": "https://www.cbc.ca/cmlink/rss-topstories",
    "The Globe and Mail": "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/canada/",
    "CTV News": "https://www.ctvnews.ca/rss/ctvnews-ca-top-stories-public-rss-1.822009",
    
    # Europe
    "DW (Deutsche Welle)": "https://rss.dw.com/rdf/rss-en-all",
    "France 24": "https://www.france24.com/en/rss",
    "Al Jazeera English": "https://www.aljazeera.com/xml/rss/all.xml",
    
    # Asia
    "The Hindu": "https://www.thehindu.com/news/feeder/default.rss",
    "Times of India": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
    "South China Morning Post": "https://www.scmp.com/rss/91/feed",
    "The Japan Times": "https://www.japantimes.co.jp/feed/",
    "The Straits Times": "https://www.straitstimes.com/news/singapore/rss.xml",
    "Bangkok Post": "https://www.bangkokpost.com/rss/data/news.xml",
    
    # Africa
    "Mail & Guardian": "https://mg.co.za/feed/",
    "News24": "https://feeds.news24.com/articles/news24/topstories/rss",
    "Daily Nation": "https://www.nation.co.ke/kenya/rss",
    
    # Latin America
    "Buenos Aires Times": "https://www.batimes.com.ar/feed",
    
    # Australia/Pacific
    "ABC News (Australia)": "https://www.abc.net.au/news/feed/51120/rss.xml",
    "The Sydney Morning Herald": "https://www.smh.com.au/rss/feed.xml",
    "RNZ": "https://www.rnz.co.nz/rss/national.xml",
    
    # Tech
    "Wired": "https://www.wired.com/feed/rss",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "TechCrunch": "https://techcrunch.com/feed/",
    "MIT Technology Review": "https://www.technologyreview.com/feed/",
    
    # Science
    "New Scientist": "https://www.newscientist.com/subject/technology/feed/",
    "Scientific American": "http://rss.sciam.com/ScientificAmerican-Global",
    "Nature News": "http://feeds.nature.com/nature/rss/current",
    "National Geographic": "https://www.nationalgeographic.com/pages/topic/latest-stories/_jcr_content.feed",
    
    # Climate/Energy
    "Canary Media": "https://www.canarymedia.com/feed",
    "Inside Climate News": "https://insideclimatenews.org/feed/",
    "Grist": "https://grist.org/feed/",
    "Carbon Brief": "https://www.carbonbrief.org/feed/",
}

def call_mistral(prompt, max_retries=3):
    """Call Mistral 7B with retry logic"""
    for attempt in range(max_retries):
        try:
            response = requests.post(
                HF_API_URL,
                headers={"Authorization": f"Bearer {HF_API_KEY}"},
                json={
                    "inputs": prompt,
                    "parameters": {
                        "max_new_tokens": 500,
                        "temperature": 0.7,
                        "return_full_text": False
                    }
                },
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                text = data[0]["generated_text"] if isinstance(data, list) else data.get("generated_text", "")
                return text.strip()
            elif response.status_code == 503:
                # Model loading
                print(f"Model loading, waiting... (attempt {attempt + 1}/{max_retries})")
                time.sleep(20)
                continue
            else:
                print(f"API error {response.status_code}: {response.text}")
                return None
                
        except Exception as e:
            print(f"Error calling Mistral: {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
            continue
    
    return None

def is_positive_article(title, summary):
    """Use Mistral to determine if article is positive"""
    prompt = f"""Is this news article genuinely POSITIVE? It should focus on: improving democracy, reducing poverty, improving education, medical advancements, positive climate news, cultural cooperation, tolerance and equity, robust courts, peace, helpful technology, positive children's news, compromise, empowering entertainment, or positive art/culture.

Title: {title}
Summary: {summary}

Respond with ONLY "YES" if it's clearly positive and uplifting, or "NO" if it's negative, polarizing, or neutral. No explanation."""

    result = call_mistral(prompt)
    if result:
        return "YES" in result.upper()
    return False

def generate_rallying_cry(title, first_paragraph):
    """Generate a short, snappy positive summary"""
    prompt = f"""Write ONE short sentence (under 15 words) that captures what makes this news positive and hopeful. Use plain, engaging English. No jargon.

Title: {title}
Article start: {first_paragraph}

Rallying Cry:"""

    result = call_mistral(prompt)
    if result:
        # Clean up the response
        cry = result.split('\n')[0].strip()
        # Remove quotes if present
        cry = cry.strip('"\'')
        return cry[:150]  # Max 150 chars
    return "Positive progress happening around the world."

def extract_first_paragraph(html):
    """Extract first substantial paragraph from article"""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove script, style, nav, header, footer
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            tag.decompose()
        
        # Find paragraphs
        paragraphs = soup.find_all('p')
        for p in paragraphs:
            text = p.get_text().strip()
            # Must be substantial (>100 chars) and not a byline/date
            if len(text) > 100 and not text.startswith(('By ', 'Published', 'Updated')):
                return text[:500]  # First 500 chars
        
        return ""
    except:
        return ""

def extract_image(html, base_url):
    """Extract main image from article"""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Try og:image first
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            return og_image['content']
        
        # Try twitter:image
        tw_image = soup.find('meta', attrs={'name': 'twitter:image'})
        if tw_image and tw_image.get('content'):
            return tw_image['content']
        
        # Find first large image
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if src and 'logo' not in src.lower() and 'icon' not in src.lower():
                return urljoin(base_url, src)
        
        return "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"
    except:
        return "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"

def scrape_article_content(url):
    """Fetch full article to extract paragraph and image"""
    try:
        response = requests.get(url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if response.status_code == 200:
            first_para = extract_first_paragraph(response.text)
            image = extract_image(response.text, url)
            return first_para, image
        return "", "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"
    except:
        return "", "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800"

def scrape_feed(feed_url, source_name):
    """Scrape RSS feed and return articles"""
    articles = []
    try:
        print(f"Scraping {source_name}...")
        feed = feedparser.parse(feed_url)
        
        for entry in feed.entries[:5]:  # Max 5 per source
            try:
                title = entry.get('title', '').strip()
                summary = entry.get('summary', entry.get('description', '')).strip()
                link = entry.get('link', '')
                
                if not title or not link:
                    continue
                
                # Check if positive with AI
                print(f"  Checking: {title[:60]}...")
                if not is_positive_article(title, summary):
                    print(f"    ❌ Not positive enough")
                    continue
                
                print(f"    ✅ Positive! Fetching full article...")
                
                # Get full article
                first_para, image = scrape_article_content(link)
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
                    "category": "home"  # Can be enhanced later
                }
                
                articles.append(article)
                print(f"    💚 Added: {rallying_cry}")
                
                time.sleep(2)  # Rate limiting
                
            except Exception as e:
                print(f"  Error processing entry: {e}")
                continue
        
    except Exception as e:
        print(f"Error scraping {source_name}: {e}")
    
    return articles

def main():
    """Main scraper function"""
    print("🌱 Rally News Scraper Starting...")
    print(f"Time: {datetime.utcnow().isoformat()}")
    
    # Select 10 random sources
    selected_sources = random.sample(list(NEWS_SOURCES.items()), min(10, len(NEWS_SOURCES)))
    print(f"\n📰 Selected sources: {[name for name, _ in selected_sources]}\n")
    
    all_articles = []
    
    for source_name, feed_url in selected_sources:
        articles = scrape_feed(feed_url, source_name)
        all_articles.extend(articles)
        time.sleep(3)  # Rate limiting between sources
    
    # Load existing articles
    try:
        with open('news.json', 'r') as f:
            existing = json.load(f)
    except:
        existing = []
    
    # Combine and deduplicate by URL
    seen_urls = {article['url'] for article in existing}
    new_articles = [a for a in all_articles if a['url'] not in seen_urls]
    
    # Prepend new articles (newest first)
    combined = new_articles + existing
    
    # Keep last 200 articles
    combined = combined[:200]
    
    # Save
    with open('news.json', 'w') as f:
        json.dump(combined, f, indent=2)
    
    print(f"\n✅ Scraping complete!")
    print(f"📊 Found {len(new_articles)} new positive articles")
    print(f"📚 Total articles in database: {len(combined)}")

if __name__ == "__main__":
    main()
