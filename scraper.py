#!/usr/bin/env python3
"""
Rally News Scraper - Fully modular version
Reads ALL configuration from: sources.json, criteria.txt, examples.json
"""
import json
import random
import time
import os
import re
from datetime import datetime
import requests
import feedparser
from bs4 import BeautifulSoup

# Load OpenRouter API key
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not set")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Try free models first, then fallback to paid
AVAILABLE_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "poolside/laguna-m.1:free",
    "openai/gpt-oss-120b:free",
    "minimax/minimax-m2.5:free",
    "inclusionai/ring-2.6-1t:free",
    "openai/gpt-oss-20b:free",
    "google/gemini-2.5-flash-lite-preview-09-2025"  # Paid fallback (last resort)
]

# Track which model we're currently using
current_model_index = 0
OPENROUTER_MODEL = AVAILABLE_MODELS[current_model_index]

print("📂 Loading configuration files...")

# Load news sources
with open('sources.json', 'r') as f:
    NEWS_SOURCES = json.load(f)
    print(f"  ✓ Loaded {len(NEWS_SOURCES)} news sources")

# Load examples
with open('examples.json', 'r') as f:
    EXAMPLES = json.load(f)
    print(f"  ✓ Loaded {len(EXAMPLES)} training examples")

# Parse criteria.txt for keywords and categories
with open('criteria.txt', 'r') as f:
    criteria_text = f.read()
    print(f"  ✓ Loaded criteria ({len(criteria_text)} chars)")

# Extract sections from criteria.txt
def parse_section(text, section_name):
    """Extract comma-separated values from a [SECTION] in criteria.txt"""
    pattern = rf'\[{section_name}\](.*?)(?=\[|$)'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        content = match.group(1).strip()
        # Split by comma and clean
        keywords = [kw.strip() for kw in content.split(',')]
        keywords = [kw for kw in keywords if kw and not kw.startswith('#')]
        return keywords
    return []

# Extract keywords from criteria.txt
NEGATIVE_KW = parse_section(criteria_text, 'NEGATIVE_KEYWORDS')
POSITIVE_KW = parse_section(criteria_text, 'POSITIVE_KEYWORDS')

print(f"  ✓ Parsed {len(NEGATIVE_KW)} negative keywords")
print(f"  ✓ Parsed {len(POSITIVE_KW)} positive keywords")

# Extract category keywords
CATEGORY_KEYWORDS = {}
for category in ['climate', 'transportation', 'ai', 'business', 'politics', 'entertainment', 'world', 'religion']:
    keywords = parse_section(criteria_text, f'CATEGORY:{category}')
    if keywords:
        CATEGORY_KEYWORDS[category] = keywords
        print(f"  ✓ Parsed {len(keywords)} keywords for '{category}'")

# Extract AI criteria (everything after the AI FILTERING CRITERIA header)
ai_criteria_match = re.search(r'# AI FILTERING CRITERIA\n# ={40}\n\n(.+)', criteria_text, re.DOTALL)
CRITERIA = ai_criteria_match.group(1).strip() if ai_criteria_match else criteria_text

print()

def quick_filter(title, summary):
    """Fast keyword pre-filter before AI"""
    title_lower = title.lower()
    full_text = (title + ' ' + summary).lower()
    
    # Check negative keywords ONLY in title
    if any(neg in title_lower for neg in NEGATIVE_KW):
        return False
    
    # Check positive keywords in title OR summary
    return any(pos in full_text for pos in POSITIVE_KW)

def detect_category(title, summary):
    """Detect article category using keywords from criteria.txt"""
    text = (title + ' ' + summary).lower()
    
    # Check each category
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    
    # Default to Arts & Culture
    return 'arts'

def call_ai(prompt):
    """Call OpenRouter API with automatic model fallback"""
    global current_model_index, OPENROUTER_MODEL
    
    # Try current model first, then fallback through the list
    models_to_try = len(AVAILABLE_MODELS)
    
    for attempt in range(models_to_try):
        try:
            r = requests.post(
                OPENROUTER_API_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/rally-news",
                    "X-Title": "Rally News Scraper"
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                    "temperature": 0.7
                },
                timeout=15
            )
            
            if r.status_code == 200:
                data = r.json()
                if 'choices' in data and len(data['choices']) > 0:
                    result = data['choices'][0]['message']['content'].strip()
                    print(f"       ✓ [{OPENROUTER_MODEL.split('/')[1][:20]}] {result[:40]}")
                    return result
            
            # Handle rate limits or errors
            elif r.status_code == 429 or r.status_code == 503:
                print(f"       ⚠️  {OPENROUTER_MODEL.split('/')[1][:30]} rate-limited, switching...")
                current_model_index = (current_model_index + 1) % len(AVAILABLE_MODELS)
                OPENROUTER_MODEL = AVAILABLE_MODELS[current_model_index]
                time.sleep(1)
                continue
            
            else:
                print(f"       Error {r.status_code}, trying next model...")
                current_model_index = (current_model_index + 1) % len(AVAILABLE_MODELS)
                OPENROUTER_MODEL = AVAILABLE_MODELS[current_model_index]
                time.sleep(1)
                continue
                
        except requests.Timeout:
            print(f"       ⏱️  Timeout, switching models...")
            current_model_index = (current_model_index + 1) % len(AVAILABLE_MODELS)
            OPENROUTER_MODEL = AVAILABLE_MODELS[current_model_index]
            time.sleep(1)
            continue
            
        except Exception as e:
            print(f"       Exception: {str(e)[:100]}, trying next...")
            current_model_index = (current_model_index + 1) % len(AVAILABLE_MODELS)
            OPENROUTER_MODEL = AVAILABLE_MODELS[current_model_index]
            time.sleep(1)
            continue
    
    # If all models failed, return None (will trigger keyword fallback)
    print(f"       ❌ All models failed, using keyword fallback")
    return None

def build_ai_prompt(title, summary, task="check"):
    """Build AI prompt using criteria.txt and examples.json"""
    
    if task == "check":
        # Show clear ACCEPT and REJECT examples from examples.json
        accept_examples = "\n".join([f"✓ \"{ex['title']}\" - {ex['why_positive']}" for ex in EXAMPLES[:5]])
        
        reject_examples = """✗ "Hurricane devastates Florida coast" - Pure disaster, no solution
✗ "Stock market plunges on recession fears" - Crisis with no progress
✗ "Teacher shortage worsens in public schools" - Problem getting worse
✗ "Tensions rise between nations over border dispute" - Conflict escalating
✗ "War casualties mount as fighting intensifies" - Violence increasing"""
        
        prompt = f"""You are a news filter. ONLY accept articles showing IMPROVEMENT or PROGRESS.

ACCEPT if showing:
- Solutions implemented
- Deals/agreements reached  
- Reforms announced
- Breakthroughs achieved
- Aid/protection provided
- Infrastructure opening
- Positive policy changes

REJECT if showing:
- Pure disasters/crises
- Problems worsening
- Conflicts escalating
- Warnings/fears without action
- Casualties/deaths
- Economic decline

ACCEPT Examples:
{accept_examples}

REJECT Examples:
{reject_examples}

Article to evaluate:
Title: {title}
Summary: {summary[:200]}

Answer ONLY "ACCEPT" or "REJECT":"""
        
        return prompt
    
    elif task == "rallying_cry":
        prompt = f"""The TITLE states WHAT happened. Write WHY it matters AND WHY it's positive in under 12 words.

TITLE: "Writers Guild reaches deal, ending strike"
RALLYING CRY: "Workers secured better conditions through collective action"

TITLE: "L.A. subway under Wilshire Boulevard opens"
RALLYING CRY: "Public transit expansion improves accessibility and reduces emissions"

TITLE: "Scientists discover link between peppers and bacteria"
RALLYING CRY: "New weapon against antibiotic-resistant infections discovered"

TITLE: {title}
RALLYING CRY:"""
        return prompt

def ai_check_positive(title, summary):
    """AI check using criteria.txt and examples.json"""
    prompt = build_ai_prompt(title, summary, task="check")
    result = call_ai(prompt)
    
    # Fallback: if AI times out, trust keywords
    if result is None:
        print(f"       ⏱️  AI timeout, trusting keywords")
        return True
    
    # Look for ACCEPT in response
    return "ACCEPT" in result.upper()[:30]

def generate_rallying_cry(title, summary):
    """Generate rallying cry explaining why it's positive"""
    prompt = build_ai_prompt(title, summary, task="rallying_cry")
    result = call_ai(prompt)
    
    if result:
        # Clean up the response
        cry = result.strip()
        
        # Remove common prefixes
        for prefix in ['RALLYING CRY:', 'Impact:', 'Why it matters:', 'The impact:', 'This shows']:
            if cry.upper().startswith(prefix.upper()):
                cry = cry[len(prefix):].strip()
        
        # Remove quotes
        cry = cry.strip('"\'')
        
        # Take first sentence
        cry = cry.split('\n')[0].split('.')[0].strip()
        
        # If it's too similar to title or too short, use fallback
        if len(cry) < 20 or cry.lower() == title.lower():
            # Fallback: extract key insight from summary
            cry = summary[:100].split('.')[0].strip()
        
        return cry[:150]
    
    # Ultimate fallback
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
            
            # Step 1: Quick keyword filter (using criteria.txt keywords)
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
            
            # Step 5: Detect category (using criteria.txt keywords)
            category = detect_category(title, summary)
            
            article = {
                "title": title,
                "source": source_name,
                "url": link,
                "first_paragraph": para,
                "rallying_cry": cry,
                "image_url": img,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "category": category
            }
            
            articles.append(article)
            print(f"     💚 [{category}] {cry}")
            time.sleep(2)
        
    except Exception as e:
        print(f"  ⚠️  Error: {e}")
    
    return articles

def main():
    print("🌱 Rally News Scraper")
    print(f"Target: 5 new articles\n")
    
    # Warm up the AI model
    print(f"🔥 Finding working AI model from {len(AVAILABLE_MODELS)} options...")
    warmup_result = call_ai("Test. Reply: OK")
    if warmup_result:
        print(f"  ✓ Using: {OPENROUTER_MODEL}\n")
    else:
        print(f"  ⚠️  All models busy, will use keyword fallback\n")
    
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
