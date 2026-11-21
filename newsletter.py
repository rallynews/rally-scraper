#!/usr/bin/env python3
"""
Bright Spots — daily newsletter compiler & sender for Rally News.

Philosophy: AI-compiled, NOT AI-generated. Real, human-written journalism that
the scraper surfaced is poured into a fixed template. An LLM writes only two
small pieces of copy: the one-paragraph intro and the three category lead-in
labels. It also picks (but never writes) the stories for the three randomly
chosen themed sections (see THEMES) and the one-per-region "Around The World"
list. Everything else is real article titles, summaries, the scraper's
Rallying Cry text, and the newsletters listed in Studio ▸ Promotion ▸ Swaps
("Friends of The Bright Spots").

Data source: the LIVE rally.news API (news.php / rallying-cry.php / friends.php),
NOT the committed JSON files in the repo (those are frozen snapshots).

Send: creates a Brevo "email campaign" and sends it immediately to the
BrightSpot list (ID 2).

Required environment variables (GitHub Actions secrets):
  NEWS_API_URL        e.g. https://rally.news/api/news.php  (base is derived from this)
  OPENROUTER_API_KEY  for the AI micro-copy (Mistral Small, same as the scraper)
  BREVO_API_KEY       for sending

Optional:
  DRY_RUN=1           build the email and write newsletter_preview.html, do NOT send
"""

import os
import re
import sys
import html
import json
import random
import datetime
import urllib.parse

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
NEWS_API_URL = os.environ.get("NEWS_API_URL", "https://rally.news/api/news.php")
API_BASE = NEWS_API_URL.rsplit("/", 1)[0]          # -> https://rally.news/api
NEWS_URL = f"{API_BASE}/news.php"
RALLYING_URL = f"{API_BASE}/rallying-cry.php"
FRIENDS_URL = f"{API_BASE}/friends.php"
# How many "Friends of The Bright Spots" each issue shows, picked at random from
# the list so a long list rotates. Mirrors PER_ISSUE in Studio's swaps.js.
FRIENDS_PER_ISSUE = 3

# Themed sections: each issue picks THEMES_PER_ISSUE of these at random and
# fills each with the article that fits it best. The keywords/categories are
# only the fallback used when the AI pick is unavailable.
THEMES = {
    "Happy & Human": {
        "cats": {"world", "entertainment"},
        "kw": ["kindness", "volunteer", "community", "hero", "donat", "rescue", "helped",
               "neighbour", "neighbor", "stranger", "charity", "inspir"]},
    "Thriving Planet": {
        "cats": {"climate"},
        "kw": ["climate", "renewable", "solar", "wind power", "forest", "ocean", "reef",
               "conservation", "species", "emission", "restor", "rewild", "clean energy"]},
    "Politics Doesn't Have to be Scary": {
        "cats": {"politics"},
        "kw": ["bill", "law", "vote", "bipartisan", "council", "government", "senate",
               "parliament", "mayor", "policy", "election"]},
    "Stories of Love": {
        "cats": set(),
        "kw": ["love", "wedding", "married", "couple", "romance", "proposal", "anniversary",
               "sweetheart", "reunite"]},
    "Animals We Can't Get Enough Of": {
        "cats": set(),
        "kw": ["dog", "cat", "puppy", "kitten", "animal", "bird", "whale", "dolphin",
               "elephant", "panda", "otter", "zoo", "wildlife", "pet", "turtle", "bear"]},
    "The Religious Spirit": {
        "cats": {"religion"},
        "kw": ["church", "faith", "pope", "mosque", "temple", "prayer", "spiritual",
               "religious", "synagogue", "monk", "pilgrim"]},
    "The Opportunities of Tech": {
        "cats": {"ai", "transportation"},
        "kw": ["ai", "robot", "tech", "app", "software", "innovation", "startup", "electric",
               "battery", "breakthrough", "space", "satellite"]},
    "World Peace": {
        "cats": {"world"},
        "kw": ["peace", "ceasefire", "treaty", "agreement", "reconcil", "diplomac", "truce",
               "talks", "cooperation", "refugee"]},
    "Arts and Crafts": {
        "cats": {"arts"},
        "kw": ["art", "artist", "craft", "museum", "paint", "sculpt", "gallery", "exhibit",
               "knit", "pottery", "music", "theatre", "theater", "festival"]},
    "Family & Friends": {
        "cats": set(),
        "kw": ["family", "friend", "mother", "father", "mom", "dad", "grand", "sibling",
               "brother", "sister", "daughter", "son", "kids", "children", "baby"]},
}
THEMES_PER_ISSUE = 3

# "Around The World": one story per region. Same regions, in the same order, as
# source_directory.RALLY_REGIONS (the values the scraper stores in `region`).
REGIONS = ("North America", "South America", "Europe", "Africa", "Asia", "Oceania")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# OpenRouter app attribution: https://openrouter.ai/docs/app-attribution
OPENROUTER_ATTRIBUTION = {"HTTP-Referer": "https://rally.news", "X-OpenRouter-Title": "Rally"}
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
DRY_RUN = os.environ.get("DRY_RUN", "").strip() not in ("", "0", "false", "False")

# Brevo
SENDER = {"name": "Rally News", "email": "brightspots@rally.news"}
LIST_ID = 2  # BrightSpot

# Brand / design
# The ONLY background colour in the email is the Rallying Cry box (BOX_BG).
# Everything else is transparent so the reader's email client theme shows through.
RALLY_GREEN = "#5A775E"
TEXT = "#1A1A1A"
MUTED = "#777777"
RULE = "#E5E5E5"
BOX_BG = "#EDF1ED"           # light green-grey, Rallying Cry box only
LOGO_URL = "https://rally.news/images/icons/brightspots.png"
HOME_URL = "https://rally.news/"
APP_URL = "https://play.google.com/store/apps/details?id=com.rallynews.rallynews"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
SERIF = "Georgia,'Times New Roman',serif"
INCLUDE_FEATURED_IMAGE = False   # flip to True if you ever want the lead image

# Dark-mode equivalents (used in the @media CSS block only)
DM_TEXT = "#E8E8E8"
DM_MUTED = "#AAAAAA"
DM_GREEN = "#90C296"         # lighter green, legible on dark backgrounds
DM_BOX   = "#1E3020"         # dark green for the Rallying Cry box
DM_RULE  = "#3A3A3A"

OR_MODEL = "mistralai/mistral-small-3.2-24b-instruct"

# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────
def clean(s: str) -> str:
    """For DISPLAY text: strip HTML tags, decode entities (twice, the data is
    sometimes double-escaped e.g. &amp;amp;), collapse whitespace."""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(html.unescape(s))
    return re.sub(r"\s+", " ", s).strip()


def esc(s: str) -> str:
    """Escape for safe insertion into our HTML."""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def reader_url(category: str, raw_title: str) -> str:
    """Build a rally.news Reader View URL. Mirrors EXACTLY how the live site
    encodes titles: decode entities once, replace spaces with '+', then
    percent-encode the whole thing (so a space becomes %2B).
    Verified against live homepage links."""
    title = html.unescape(raw_title or "")
    enc = urllib.parse.quote(title.replace(" ", "+"), safe="")
    return f"https://rally.news/categories/{category}/?article={enc}"


def get_json(url, **params):
    r = requests.get(url, params=params or None, timeout=30)
    r.raise_for_status()
    return r.json()


def ai(prompt: str, max_tokens: int = 300, temperature: float = 0.7) -> str:
    """One call to OpenRouter (Mistral Small). Raises on failure; callers catch
    and fall back so a flaky model never blocks the send."""
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                 "Content-Type": "application/json",
                 **OPENROUTER_ATTRIBUTION},
        json={
            "model": OR_MODEL,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system",
                 "content": ("You are a concise editor for a positive-news email brand "
                             "called Bright Spots. Output ONLY what is asked — no preamble, "
                             "no markdown, no quotation marks around your answer.")},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def parse_api_entry(entry):
    """The PHP backend may store the entire POSTed JSON payload as a single
    text string in the `content` column.  The AI model may also wrap the JSON
    in markdown code fences (```json ... ```), and the stored value may be
    truncated (MySQL field length limit).  Handle all three cases:
      1. Complete fenced JSON   → strip fences, json.loads, unpack
      2. Truncated fenced JSON  → strip opening fence only, regex-extract content
      3. Complete bare JSON     → json.loads, unpack
    """
    if not entry:
        return entry
    raw = entry.get("content", "")
    # Strip opening code fence regardless of whether the closing fence exists
    # (truncated content may be missing the closing fence)
    stripped = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    stripped = re.sub(r"\s*```\s*$", "", stripped)  # closing fence if present

    if not stripped.startswith("{"):
        return entry

    # Fast path: complete, valid JSON
    try:
        inner = json.loads(stripped)
        if isinstance(inner, dict) and "content" in inner:
            return {
                "date": entry.get("date"),
                "timestamp": entry.get("timestamp"),
                "content": inner.get("content", ""),
                "stories": inner.get("stories") or entry.get("stories") or [],
            }
    except (json.JSONDecodeError, ValueError):
        pass

    # Slow path: JSON is truncated — regex-extract just the "content" value.
    # Pattern matches the string value of the "content" key, handling \" escapes.
    m = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', stripped)
    if m:
        raw_val = m.group(1)
        try:
            # Decode JSON string escapes (\" → ", \n → newline, etc.)
            content_text = json.loads(f'"{raw_val}"')
        except (json.JSONDecodeError, ValueError):
            content_text = raw_val
        return {
            "date": entry.get("date"),
            "timestamp": entry.get("timestamp"),
            "content": content_text,
            "stories": entry.get("stories") or [],
        }

    return entry


# ─────────────────────────────────────────────────────────────────────────────
# Data selection
# ─────────────────────────────────────────────────────────────────────────────

# Category tiers used as a fallback when AI selection fails.
_FEATURED_TIER1 = {"world", "climate", "business", "ai"}
_FEATURED_TIER2 = {"politics", "transportation", "arts", "religion"}
# entertainment (sports lives here) is lowest priority for featured

# Broad interest clusters used to maximise diversity in the three "more" picks.
_MORE_CLUSTERS = [
    {"world", "climate", "politics"},
    {"business", "ai", "transportation"},
    {"entertainment", "arts", "religion"},
]


def _ai_pick_featured(candidates):
    """Ask the LLM to choose the best featured story from a shortlist.
    Returns the chosen article dict, or None on any failure."""
    lines = "\n".join(
        f"{i+1}. [{a.get('category', '')}] {clean(a.get('title', ''))}"
        for i, a in enumerate(candidates)
    )
    try:
        raw = ai(
            "From the numbered stories below choose the ONE that best fits as the "
            "featured story for an international positive-news email. "
            "Prioritise worldwide or international significance — climate, business, "
            "science, AI, or celebrity news. Avoid personal interest stories, local "
            "sports, recipes, or stories with only regional appeal. "
            "Reply with ONLY the number (e.g. ‘3’), nothing else.\n\n" + lines,
            max_tokens=10,
            temperature=0.2,
        )
        m = re.search(r"\d+", raw)
        if m:
            idx = int(m.group()) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
    except Exception as e:
        print(f"[warn] featured AI selection failed: {e}", file=sys.stderr)
    return None


def _fallback_featured(candidates):
    """Rule-based featured pick when AI is unavailable: tier-1 categories first."""
    for tier in (_FEATURED_TIER1, _FEATURED_TIER2):
        for a in candidates:
            if a.get("category") in tier:
                return a
    return candidates[0]


def pick_articles(news):
    """
    Featured: AI picks the most internationally significant story from up to 15
    candidates (prioritising climate, business, world, science/AI, celebrity);
    falls back to category-tier rules if the AI call fails.

    More: 3 additional articles chosen to span a wide range of reader interests.
    A cluster-based pass ensures one pick per broad theme (global affairs /
    economy+tech / culture+lifestyle), with fallback passes to top up to 3.
    """
    if not news:
        return None, []

    # Rally Originals are staff-written pieces, not scraped journalism — exclude
    # them from the newsletter's article picks entirely. The API returns this
    # flag as a string ("0"/"1") since the PHP backend uses emulated prepares,
    # so compare against the stringified value rather than Python truthiness.
    news = [a for a in news if str(a.get("rally_originals", 0)).strip() not in ("1", "true", "True")]
    if not news:
        return None, []

    candidates = [a for a in news if clean(a.get("summary") or a.get("content"))]
    if not candidates:
        candidates = list(news)

    featured = _ai_pick_featured(candidates[:15]) or _fallback_featured(candidates)

    used_cats = {featured.get("category")}
    pool = [a for a in news if a is not featured]
    more = []

    # First pass: one article per broad interest cluster
    for cluster in _MORE_CLUSTERS:
        if len(more) == 3:
            break
        for a in pool:
            if a in more:
                continue
            c = a.get("category")
            if c and c in cluster and c not in used_cats:
                more.append(a)
                used_cats.add(c)
                break

    # Second pass: any remaining unused category
    if len(more) < 3:
        for a in pool:
            if a in more:
                continue
            c = a.get("category")
            if c and c not in used_cats:
                more.append(a)
                used_cats.add(c)
            if len(more) == 3:
                break

    # Final fallback: any distinct article so the section is never short
    if len(more) < 3:
        for a in pool:
            if a not in more:
                more.append(a)
            if len(more) == 3:
                break

    return featured, more


def _is_original(a):
    return str(a.get("rally_originals", 0)).strip() in ("1", "true", "True")


def _summary(a):
    return clean(a.get("summary") or a.get("content") or "")


def _numbered(articles, with_region=False):
    lines = []
    for i, a in enumerate(articles):
        tag = f"[{a.get('category', '')}]"
        if with_region and a.get("region") in REGIONS:
            tag += f" [{a['region']}]"
        lines.append(f"{i+1}. {tag} {clean(a.get('title', ''))} — {_summary(a)[:160]}")
    return "\n".join(lines)


def _ai_json(prompt, max_tokens=200):
    raw = ai(prompt, max_tokens=max_tokens, temperature=0.2)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group() if m else raw)


def _theme_score(a, spec):
    """Fallback fit score: keyword hits (short keywords must be whole words),
    plus a point for a matching category."""
    text = f"{clean(a.get('title', ''))} {_summary(a)}".lower()
    words = set(re.findall(r"[a-z']+", text))
    hits = sum(1 for k in spec["kw"] if (k in words if len(k) <= 3 else k in text))
    return hits * 2 + (1 if a.get("category") in spec["cats"] else 0)


def pick_themed(news, exclude, k=THEMES_PER_ISSUE, rng=random):
    """Randomly choose k themes and the article that best fits each.

    Returns [(theme, article), ...]. The AI matches stories to themes; a keyword
    and category score is the fallback. A theme that nothing fits is swapped for
    another so the issue still gets k sections when the pool allows."""
    pool = [a for a in news if not _is_original(a) and a.get("url") not in exclude
            and _summary(a)][:40]
    if not pool:
        return []
    order = rng.sample(list(THEMES), len(THEMES))
    chosen, spare = order[:k], order[k:]

    picks = {}
    try:
        got = _ai_json(
            "Below are numbered good-news stories, then section themes for a positive-news "
            "email. For EACH theme pick the ONE story that fits it best. Use a story only "
            "once. If no story genuinely fits a theme, use null for it.\n\nThemes: "
            + json.dumps(chosen) + "\n\nStories:\n" + _numbered(pool)
            + "\n\nReply with ONLY a JSON object mapping each theme to a story number, "
            'e.g. {"Theme A": 4, "Theme B": null}.',
            max_tokens=150)
        for theme in chosen:
            n = got.get(theme)
            if isinstance(n, (int, str)) and str(n).strip().isdigit():
                idx = int(n) - 1
                if 0 <= idx < len(pool) and pool[idx] not in picks.values():
                    picks[theme] = pool[idx]
    except Exception as e:
        print(f"[warn] themed AI selection failed: {e}", file=sys.stderr)

    # Keyword fallback for any theme the AI left empty; a theme nothing fits is
    # swapped for a spare one.
    result, taken = [], set()
    queue = list(chosen)
    while queue and len(result) < k:
        theme = queue.pop(0)
        art = picks.get(theme)
        if art is None or id(art) in taken:
            reserved = {id(x) for t, x in picks.items() if t in queue}
            scored = [(_theme_score(x, THEMES[theme]), -i, x) for i, x in enumerate(pool)
                      if id(x) not in taken and id(x) not in reserved]
            best = max(scored, key=lambda x: x[:2], default=None)
            art = best[2] if best and best[0] > 0 else None
        if art is None:
            if spare:
                queue.append(spare.pop(0))
            continue
        result.append((theme, art))
        taken.add(id(art))
    return result


def pick_world(news, exclude):
    """One story per region, as [(region, article), ...] in REGIONS order.

    Uses the article's stored `region` (set by the scraper) where the API serves
    it; the AI assigns regions to the rest. Regions with no story are left out."""
    pool = [a for a in news if not _is_original(a) and a.get("url") not in exclude][:80]
    if not pool:
        return []
    picks = {}
    try:
        got = _ai_json(
            "Below are numbered good-news stories. Some are tagged with the world region "
            "they cover. For EACH of these regions pick the ONE most uplifting, interesting "
            "story that takes place in or mainly affects that region: "
            + ", ".join(REGIONS) + ". A story tagged with a region may only be used for that "
            "region. Use each story once. Use null if no story fits a region.\n\nStories:\n"
            + _numbered(pool, with_region=True)
            + "\n\nReply with ONLY a JSON object mapping each region to a story number.",
            max_tokens=150)
        for region in REGIONS:
            n = got.get(region)
            if isinstance(n, (int, str)) and str(n).strip().isdigit():
                idx = int(n) - 1
                if not 0 <= idx < len(pool):
                    continue
                a = pool[idx]
                if a.get("region") in REGIONS and a["region"] != region:
                    continue  # contradicts the scraper's own region
                if a not in picks.values():
                    picks[region] = a
    except Exception as e:
        print(f"[warn] world AI selection failed: {e}", file=sys.stderr)

    # Fill gaps from the stored region (newest first).
    for region in REGIONS:
        if region in picks:
            continue
        for a in pool:
            if a.get("region") == region and a not in picks.values():
                picks[region] = a
                break
    return [(r, picks[r]) for r in REGIONS if r in picks]


def cry_story_link(story, url_to_article):
    """Prefer an in-ecosystem Reader View link (needs the article's category,
    recovered by matching the story URL against the news feed); fall back to the
    original source URL the cry stored."""
    title = clean(story.get("title", ""))
    src = story.get("url", "")
    match = url_to_article.get(src)
    href = reader_url(match["category"], story.get("title", "")) if match else src
    return href, title


# ─────────────────────────────────────────────────────────────────────────────
# AI micro-copy (with safe fallbacks)
# ─────────────────────────────────────────────────────────────────────────────
def make_intro(items):
    lines = "\n".join(f"{i+1}. {clean(a.get('title',''))}" for i, a in enumerate(items))
    try:
        return ai(
            "Write a single warm paragraph (2–3 sentences) that briefly previews "
            "the four good-news stories below for the top of a positive-news email. "
            "Flow them together naturally; do NOT use a list. Do not write ‘newsletter’ "
            "or ‘in this edition’.\n\n" + lines,
            max_tokens=180,
        )
    except Exception as e:
        print(f"[warn] intro AI failed: {e}", file=sys.stderr)
        return ("A few bright spots to start your day — a handful of genuinely good "
                "stories from trusted newsrooms around the world.")


def make_labels(more):
    items = "\n".join(
        f"{i+1}. [{a.get('category','')}] {clean(a.get('title',''))}" for i, a in enumerate(more)
    )
    fallback = ["Also good today:", "Worth a smile:", "More good news:"]
    try:
        raw = ai(
            "For each story below write a short, friendly lead-in phrase (2–5 words) "
            "that would sit just before the headline in a good-news email, matched to "
            "the story’s category and tone. Style examples: ‘For sports fans:’, "
            "‘Foodies will love’, ‘Good climate news:’. "
            "Return ONLY a JSON array of strings, in order, nothing else.\n\n" + items,
            max_tokens=120, temperature=0.8,
        )
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
        labels = json.loads(raw)
        labels = [str(x).strip() for x in labels][: len(more)]
        while len(labels) < len(more):
            labels.append(fallback[len(labels) % 3])
        return labels
    except Exception as e:
        print(f"[warn] labels AI failed: {e}", file=sys.stderr)
        return [fallback[i % 3] for i in range(len(more))]


# ─────────────────────────────────────────────────────────────────────────────
# HTML assembly (pure function — easy to preview/test)
# ─────────────────────────────────────────────────────────────────────────────
# Dark-mode CSS class legend (applied alongside inline styles):
#   dm-t  → main text colour        light: #1A1A1A  dark: #E8E8E8
#   dm-m  → muted text colour       light: #777777  dark: #AAAAAA
#   dm-g  → green accent colour     light: #5A775E  dark: #90C296
#   dm-b  → Rallying Cry box bg     light: #EDF1ED  dark: #1E3020
#   dm-r  → horizontal rule border  light: #E5E5E5  dark: #3A3A3A
def build_html(featured, more, labels, intro, cry, friends, url_to_article, used_urls=None,
               themed=None, world=None):
    today = datetime.date.today().strftime("%A, %B %-d, %Y")

    f_title = clean(featured.get("title", ""))
    f_source = clean(featured.get("source", ""))
    f_summary = clean(featured.get("summary") or featured.get("content") or "")
    f_url = reader_url(featured.get("category", ""), featured.get("title", ""))
    f_head = f"From {f_source}, {f_title}" if f_source else f_title

    # Featured (optional) image
    img_block = ""
    if INCLUDE_FEATURED_IMAGE and featured.get("image_url"):
        img_block = (
            f'<tr><td style="padding:0 0 18px;">'
            f'<img src="{esc(featured["image_url"])}" width="600" alt="" '
            f'style="display:block;width:100%;max-width:600px;height:auto;border:0;border-radius:6px;"></td></tr>'
        )

    # "More Good News Today" rows
    more_rows = ""
    for a, label in zip(more, labels):
        t = clean(a.get("title", ""))
        u = reader_url(a.get("category", ""), a.get("title", ""))
        more_rows += (
            f'<tr><td class="dm-t" style="padding:7px 0;font:16px/1.5 {FONT};color:{TEXT};">'
            f'<strong class="dm-g" style="color:{RALLY_GREEN};">{esc(label)}</strong> '
            f'<a href="{esc(u)}" class="dm-t" style="color:{TEXT};text-decoration:underline;">{esc(t)}</a>'
            f'</td></tr>'
        )

    # Rallying Cry box
    cry_block = ""
    if cry and clean(cry.get("content", "")):
        stories_li = ""
        for s in cry.get("stories", []) or []:
            if used_urls and s.get("url") in used_urls:
                continue  # already featured in the main content
            href, title = cry_story_link(s, url_to_article)
            if not title:
                continue
            stories_li += (
                f'<li style="margin:4px 0;">'
                f'<a href="{esc(href)}" class="dm-g" style="color:{RALLY_GREEN};text-decoration:underline;">{esc(title)}</a>'
                f'</li>'
            )
        stories_ul = (
            f'<ul class="dm-t" style="margin:12px 0 0;padding-left:20px;font:15px/1.5 {FONT};color:{TEXT};">{stories_li}</ul>'
            if stories_li else ""
        )
        cry_block = (
            f'<tr><td style="padding:8px 0 28px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'class="dm-b" style="background:{BOX_BG};border-radius:8px;">'
            f'<tr><td style="padding:22px 24px;">'
            f'<p class="dm-t" style="margin:0 0 10px;font:16px/1.5 {FONT};color:{TEXT};">'
            f'<strong class="dm-g" style="color:{RALLY_GREEN};">Today’s Rallying Cry:</strong> '
            f'Here’s an AI-generated overview of recent good news stories from the last 24 hours.</p>'
            f'<p class="dm-t" style="margin:0;font:17px/1.6 {SERIF};color:{TEXT};">{esc(clean(cry["content"]))}</p>'
            f'{stories_ul}'
            f'</td></tr></table></td></tr>'
        )

    # Themed sections (randomly chosen each issue) — headline, summary, link.
    themed_block = ""
    for theme, a in themed or []:
        t = clean(a.get("title", ""))
        u = reader_url(a.get("category", ""), a.get("title", ""))
        themed_block += (
            f'<tr><td class="dm-g" style="padding:0 0 6px;font:bold 13px/1.3 {FONT};color:{RALLY_GREEN};'
            f'text-transform:uppercase;letter-spacing:.6px;">{esc(theme)}</td></tr>'
            f'<tr><td class="dm-t" style="padding:0 0 6px;font:bold 19px/1.3 {SERIF};color:{TEXT};">'
            f'<a href="{esc(u)}" class="dm-t" style="color:{TEXT};text-decoration:none;">{esc(t)}</a></td></tr>'
            f'<tr><td class="dm-t" style="padding:0 0 8px;font:16px/1.6 {FONT};color:{TEXT};">{esc(_summary(a))}</td></tr>'
            f'<tr><td style="padding:0 0 26px;">'
            f'<a href="{esc(u)}" class="dm-g" style="font:bold 15px/1 {FONT};color:{RALLY_GREEN};text-decoration:none;">Read on Rally &rarr;</a>'
            f'</td></tr>'
        )

    # Around The World — one story per region.
    world_block = ""
    world_rows = ""
    for region, a in world or []:
        t = clean(a.get("title", ""))
        u = reader_url(a.get("category", ""), a.get("title", ""))
        world_rows += (
            f'<tr><td class="dm-t" style="padding:7px 0;font:16px/1.5 {FONT};color:{TEXT};">'
            f'<strong class="dm-g" style="color:{RALLY_GREEN};">{esc(region)}:</strong> '
            f'<a href="{esc(u)}" class="dm-t" style="color:{TEXT};text-decoration:underline;">{esc(t)}</a>'
            f'</td></tr>'
        )
    if world_rows:
        world_block = (
            f'<tr><td class="dm-r" style="padding:0 0 6px;border-top:1px solid {RULE};"></td></tr>'
            f'<tr><td class="dm-t" style="padding:14px 0 6px;font:bold 18px/1.3 {SERIF};color:{TEXT};">Around The World</td></tr>'
            f'<tr><td style="padding:0 0 28px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{world_rows}</table></td></tr>'
        )

    # Friends of The Bright Spots — other positive newsletters (Studio ▸ Promotion ▸ Swaps).
    # Left out entirely when the list is empty.
    friends_li = ""
    for fr in friends or []:
        name, link = clean(fr.get("name", "")), (fr.get("link") or "").strip()
        if not name or not link.lower().startswith(("http://", "https://")):
            continue
        friends_li += (
            f'<li style="margin:4px 0;">'
            f'<a href="{esc(link)}" class="dm-g" style="color:{RALLY_GREEN};text-decoration:underline;">{esc(name)}</a>'
            f'</li>'
        )
    friends_block = ""
    if friends_li:
        friends_block = (
            f'<tr><td class="dm-t" style="padding:0 0 8px;font:bold 18px/1.3 {SERIF};color:{TEXT};">Friends of The Bright Spots</td></tr>'
            f'<tr><td class="dm-t" style="padding:0 0 4px;font:16px/1.6 {FONT};color:{TEXT};">'
            f'Here are some other awesome positive newsletters we think you might love:</td></tr>'
            f'<tr><td style="padding:0 0 28px;">'
            f'<ul class="dm-t" style="margin:4px 0 0;padding-left:20px;font:16px/1.6 {FONT};color:{TEXT};">{friends_li}</ul>'
            f'</td></tr>'
        )

    return f"""\
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<title>Bright Spots</title>
<style type="text/css">
@media (prefers-color-scheme:dark){{
  .dm-t{{color:{DM_TEXT} !important}}
  .dm-m{{color:{DM_MUTED} !important}}
  .dm-g{{color:{DM_GREEN} !important}}
  .dm-b{{background-color:{DM_BOX} !important}}
  .dm-r{{border-color:{DM_RULE} !important}}
}}
</style>
</head>
<body style="margin:0;padding:0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;">

  <!-- Masthead: logo only, no header bar -->
  <tr><td align="center" style="padding:8px 0 4px;">
    <img src="{LOGO_URL}" alt="Bright Spots" width="600"
         style="display:block;width:100%;max-width:600px;height:auto;border:0;">
  </td></tr>
  <tr><td align="center" class="dm-m" style="padding:0 0 22px;font:13px/1.4 {FONT};color:{MUTED};letter-spacing:.3px;">{today}</td></tr>

  <!-- Intro paragraph -->
  <tr><td class="dm-t" style="padding:0 0 26px;font:17px/1.6 {FONT};color:{TEXT};">{esc(intro)}</td></tr>

  <!-- Featured -->
  {img_block}
  <tr><td class="dm-t" style="padding:0 0 8px;font:bold 23px/1.3 {SERIF};color:{TEXT};">
    <a href="{esc(f_url)}" class="dm-t" style="color:{TEXT};text-decoration:none;">{esc(f_head)}</a>
  </td></tr>
  <tr><td class="dm-t" style="padding:0 0 12px;font:16px/1.6 {FONT};color:{TEXT};">{esc(f_summary)}</td></tr>
  <tr><td style="padding:0 0 30px;">
    <a href="{esc(f_url)}" class="dm-g" style="font:bold 15px/1 {FONT};color:{RALLY_GREEN};text-decoration:none;">Continue Reading &rarr;</a>
  </td></tr>

  <!-- More Good News Today -->
  <tr><td class="dm-r" style="padding:0 0 6px;border-top:1px solid {RULE};"></td></tr>
  <tr><td class="dm-t" style="padding:14px 0 6px;font:bold 18px/1.3 {SERIF};color:{TEXT};">More Good News Today</td></tr>
  <tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{more_rows}</table></td></tr>
  <tr><td style="padding:14px 0 30px;">
    <a href="{HOME_URL}" class="dm-g" style="font:bold 15px/1 {FONT};color:{RALLY_GREEN};text-decoration:none;">Continue reading good news &rarr;</a>
  </td></tr>

  <!-- Rallying Cry (only coloured box) -->
  {cry_block}

  <!-- Themed sections -->
  {themed_block}

  <!-- Around The World -->
  {world_block}

  <!-- Friends of The Bright Spots -->
  {friends_block}

  <!-- App promo -->
  <tr><td class="dm-r" style="padding:8px 0 0;border-top:1px solid {RULE};"></td></tr>
  <tr><td align="center" class="dm-t" style="padding:22px 0 6px;font:italic 16px/1.5 {SERIF};color:{TEXT};">
    Want more good news?
    <a href="{APP_URL}" class="dm-g" style="color:{RALLY_GREEN};text-decoration:underline;">Download the Rally News app</a>
    to stay even happier every day.
  </td></tr>

  <!-- AI transparency note -->
  <tr><td class="dm-m dm-r" style="padding:20px 0 0;border-top:1px solid {RULE};font:13px/1.6 {FONT};color:{MUTED};">
    <b>The news in this email was made by people, but the newsletter was compiled by AI.</b>
    We&#8217;re a very small team at Rally with limited resources. We aim to hire full time editors
    in the future, but for now, our newsletters are put together by AI. The actual news, however,
    is always made by humans.
  </td></tr>

  <!-- Footer -->
  <tr><td align="center" class="dm-m" style="padding:24px 0 8px;font:12px/1.6 {FONT};color:{MUTED};">
    You&#8217;re receiving this because you subscribed to Bright Spots from Rally News.<br>
    <a href="{{{{ unsubscribe }}}}" class="dm-m" style="color:{MUTED};text-decoration:underline;">Unsubscribe</a>
    &nbsp;&middot;&nbsp; <a href="{HOME_URL}" class="dm-m" style="color:{MUTED};text-decoration:underline;">rally.news</a>
  </td></tr>

</table></td></tr></table>
</body></html>"""


def pick_friends(friends, k=FRIENDS_PER_ISSUE):
    """Up to k different newsletters, at random, from the Swaps list. Rows with
    no name or no web link are skipped; the same link never appears twice."""
    seen, usable = set(), []
    for fr in friends:
        name = clean((fr or {}).get("name", ""))
        link = ((fr or {}).get("link") or "").strip()
        if not name or not link.lower().startswith(("http://", "https://")) or link.lower() in seen:
            continue
        seen.add(link.lower())
        usable.append({"name": name, "link": link})
    return random.sample(usable, min(k, len(usable)))


def build_subject(featured):
    t = clean(featured.get("title", ""))
    if len(t) > 90:
        t = t[:87].rstrip() + "…"
    return f"Bright Spots: {t} & 2 more pieces of good news."


# ─────────────────────────────────────────────────────────────────────────────
# Brevo send
# ─────────────────────────────────────────────────────────────────────────────
def send_campaign(subject, html_content):
    headers = {"api-key": BREVO_API_KEY, "Content-Type": "application/json", "accept": "application/json"}
    create = requests.post(
        "https://api.brevo.com/v3/emailCampaigns",
        headers=headers,
        json={
            "name": f"Bright Spots {datetime.date.today().isoformat()}",
            "subject": subject,
            "sender": SENDER,
            "htmlContent": html_content,
            "recipients": {"listIds": [LIST_ID]},
        },
        timeout=60,
    )
    create.raise_for_status()
    campaign_id = create.json()["id"]
    send = requests.post(
        f"https://api.brevo.com/v3/emailCampaigns/{campaign_id}/sendNow",
        headers=headers, timeout=60,
    )
    send.raise_for_status()
    return campaign_id


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    news = get_json(NEWS_URL, limit=150)
    featured, more = pick_articles(news)
    if not featured:
        print("No articles available — aborting.", file=sys.stderr)
        sys.exit(1)

    url_to_article = {a.get("url"): a for a in news if a.get("url")}

    try:
        cry = parse_api_entry((get_json(RALLYING_URL, limit=1) or [None])[0])
    except Exception as e:
        print(f"[warn] rallying cry fetch failed: {e}", file=sys.stderr); cry = None
    try:
        friends = pick_friends((get_json(FRIENDS_URL, action="list") or {}).get("friends") or [])
    except Exception as e:
        print(f"[warn] friends fetch failed: {e}", file=sys.stderr); friends = []

    intro = make_intro([featured] + more)
    labels = make_labels(more)

    # Only exclude the featured article from the Rallying Cry bullet list —
    # it has the most prominent placement (headline + summary + link), so
    # repeating it below would be redundant. "More" articles are brief enough
    # that a Rallying Cry cross-reference adds context rather than repetition.
    used_urls = {featured.get("url")} if featured.get("url") else set()

    # Themed sections and Around The World never repeat a story shown above them.
    shown = {a.get("url") for a in [featured] + more if a.get("url")}
    themed = pick_themed(news, shown)
    shown |= {a.get("url") for _, a in themed}
    world = pick_world(news, shown)
    print("Themes: " + ", ".join(t for t, _ in themed))
    print("Regions: " + ", ".join(r for r, _ in world))

    subject = build_subject(featured)
    html_out = build_html(featured, more, labels, intro, cry, friends, url_to_article, used_urls,
                          themed=themed, world=world)

    if DRY_RUN:
        with open("newsletter_preview.html", "w", encoding="utf-8") as f:
            f.write(html_out)
        print(f"DRY RUN — wrote newsletter_preview.html ({len(html_out)} bytes)")
        print(f"Subject: {subject}")
        return

    campaign_id = send_campaign(subject, html_out)
    print(f"Sent Bright Spots campaign {campaign_id} to list {LIST_ID}.")


if __name__ == "__main__":
    main()
