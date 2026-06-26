#!/usr/bin/env python3
"""
Bright Spots — daily newsletter compiler & sender for Rally News.

Philosophy: AI-compiled, NOT AI-generated. Real, human-written journalism that
the scraper surfaced is poured into a fixed template. An LLM writes only three
small things: the one-paragraph intro, the three category lead-in labels, and
the closing line. Everything else is real article titles, summaries, and the
scraper's Rallying Cry / On Balance text.

Data source: the LIVE rally.news API (news.php / rallying-cry.php / balance.php),
NOT the committed JSON files in the repo (those are frozen snapshots).

Send: creates a Brevo "email campaign" and sends it immediately to the
BrightSpot list (ID 2).

Required environment variables (GitHub Actions secrets):
  NEWS_API_URL        e.g. https://rally.news/api/news.php  (base is derived from this)
  OPENROUTER_API_KEY  for the 3 small AI text pieces (Mistral Small, like the scraper)
  BREVO_API_KEY       for sending

Optional:
  DRY_RUN=1           build the email and write newsletter_preview.html, do NOT send
"""

import os
import re
import sys
import html
import json
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
BALANCE_URL = f"{API_BASE}/balance.php"

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
DRY_RUN = os.environ.get("DRY_RUN", "").strip() not in ("", "0", "false", "False")

# Brevo
SENDER = {"name": "Bright Spots", "email": "brightspots@rally.news"}
LIST_ID = 2  # BrightSpot

# Brand / design (the ONLY background colour anywhere is the Rallying Cry box)
RALLY_GREEN = "#5A775E"
TEXT = "#1A1A1A"
MUTED = "#777777"
RULE = "#E5E5E5"
BOX_BG = "#EDF1ED"           # light green-grey, Rallying Cry box only
LOGO_URL = "https://rally.news/images/icons/brightspots.png"
HOME_URL = "https://rally.news/"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
SERIF = "Georgia,'Times New Roman',serif"
INCLUDE_FEATURED_IMAGE = False   # flip to True if you ever want the lead image

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
    encodes titles: decode entities, replace spaces with '+', then percent-encode
    the whole thing (so a space becomes %2B). Verified against live homepage links."""
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
                 "Content-Type": "application/json"},
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


# ─────────────────────────────────────────────────────────────────────────────
# Data selection
# ─────────────────────────────────────────────────────────────────────────────
def pick_articles(news):
    """Featured = newest article with a usable summary. Then up to 3 more, each
    from a DIFFERENT category (and different from the featured's category)."""
    if not news:
        return None, []

    featured = next((a for a in news if clean(a.get("summary") or a.get("content"))), news[0])

    used = {featured.get("category")}
    more = []
    for a in news:
        if a is featured:
            continue
        c = a.get("category")
        if c and c not in used:
            more.append(a)
            used.add(c)
        if len(more) == 3:
            break
    # Fallback: if there weren't 3 distinct categories, top up with any other
    # distinct articles so the section is never short.
    if len(more) < 3:
        for a in news:
            if a is featured or a in more:
                continue
            more.append(a)
            if len(more) == 3:
                break
    return featured, more


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
            "Flow them together naturally; do NOT use a list. Do not write 'newsletter' "
            "or 'in this edition'.\n\n" + lines,
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
            "the story's category and tone. Style examples: 'For sports fans:', "
            "'Foodies will love', 'Good climate news:'. "
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


def make_signoff():
    try:
        return ai(
            "Write one short, warm closing line (max 18 words) for a positive-news email, "
            "reminding the reader to stay positive and have a good day. No emojis.",
            max_tokens=60, temperature=0.8,
        )
    except Exception as e:
        print(f"[warn] signoff AI failed: {e}", file=sys.stderr)
        return "Carry a little of this with you — stay positive, and have a wonderful day."


# ─────────────────────────────────────────────────────────────────────────────
# HTML assembly (pure function — easy to preview/test)
# ─────────────────────────────────────────────────────────────────────────────
def build_html(featured, more, labels, intro, cry, balance, signoff, url_to_article):
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
            f'<tr><td style="padding:7px 0;font:16px/1.5 {FONT};color:{TEXT};">'
            f'<strong style="color:{RALLY_GREEN};">{esc(label)}</strong> '
            f'<a href="{esc(u)}" style="color:{TEXT};text-decoration:underline;">{esc(t)}</a>'
            f'</td></tr>'
        )

    # Rallying Cry box
    cry_block = ""
    if cry and clean(cry.get("content", "")):
        stories_li = ""
        for s in cry.get("stories", []) or []:
            href, title = cry_story_link(s, url_to_article)
            if not title:
                continue
            stories_li += (
                f'<li style="margin:4px 0;">'
                f'<a href="{esc(href)}" style="color:{RALLY_GREEN};text-decoration:underline;">{esc(title)}</a>'
                f'</li>'
            )
        stories_ul = (
            f'<ul style="margin:12px 0 0;padding-left:20px;font:15px/1.5 {FONT};color:{TEXT};">{stories_li}</ul>'
            if stories_li else ""
        )
        cry_block = (
            f'<tr><td style="padding:8px 0 28px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="background:{BOX_BG};border-radius:8px;">'
            f'<tr><td style="padding:22px 24px;">'
            f'<p style="margin:0 0 10px;font:16px/1.5 {FONT};color:{TEXT};">'
            f'<strong style="color:{RALLY_GREEN};">Today’s Rallying Cry:</strong> '
            f"Here’s an AI-generated overview of recent good news stories from the last 24 hours.</p>"
            f'<p style="margin:0;font:17px/1.6 {SERIF};color:{TEXT};">{esc(clean(cry["content"]))}</p>'
            f'{stories_ul}'
            f'</td></tr></table></td></tr>'
        )

    # On Balance (paragraph only, no links)
    balance_block = ""
    if balance and clean(balance.get("content", "")):
        balance_block = (
            f'<tr><td style="padding:0 0 8px;font:bold 18px/1.3 {SERIF};color:{TEXT};">On Balance</td></tr>'
            f'<tr><td style="padding:0 0 8px;font:14px/1.4 {FONT};color:{MUTED};">'
            f"Here’s the not-so-good news happening in the world today.</td></tr>"
            f'<tr><td style="padding:0 0 28px;font:16px/1.6 {FONT};color:{TEXT};">'
            f'{esc(clean(balance["content"]))}</td></tr>'
        )

    return f"""\
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<title>Bright Spots</title></head>
<body style="margin:0;padding:0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">

  <!-- Masthead: logo only, no header bar -->
  <tr><td align="center" style="padding:8px 0 4px;">
    <img src="{LOGO_URL}" alt="Bright Spots" width="180"
         style="display:block;width:180px;max-width:60%;height:auto;border:0;">
  </td></tr>
  <tr><td align="center" style="padding:0 0 22px;font:13px/1.4 {FONT};color:{MUTED};letter-spacing:.3px;">{today}</td></tr>

  <!-- Intro paragraph -->
  <tr><td style="padding:0 0 26px;font:17px/1.6 {FONT};color:{TEXT};">{esc(intro)}</td></tr>

  <!-- Featured -->
  {img_block}
  <tr><td style="padding:0 0 8px;font:bold 23px/1.3 {SERIF};color:{TEXT};">
    <a href="{esc(f_url)}" style="color:{TEXT};text-decoration:none;">{esc(f_head)}</a>
  </td></tr>
  <tr><td style="padding:0 0 12px;font:16px/1.6 {FONT};color:{TEXT};">{esc(f_summary)}</td></tr>
  <tr><td style="padding:0 0 30px;">
    <a href="{esc(f_url)}" style="font:bold 15px/1 {FONT};color:{RALLY_GREEN};text-decoration:none;">Continue Reading &rarr;</a>
  </td></tr>

  <!-- More Good News Today -->
  <tr><td style="padding:0 0 6px;border-top:1px solid {RULE};"></td></tr>
  <tr><td style="padding:14px 0 6px;font:bold 18px/1.3 {SERIF};color:{TEXT};">More Good News Today</td></tr>
  <tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{more_rows}</table></td></tr>
  <tr><td style="padding:14px 0 30px;">
    <a href="{HOME_URL}" style="font:bold 15px/1 {FONT};color:{RALLY_GREEN};text-decoration:none;">Continue reading good news &rarr;</a>
  </td></tr>

  <!-- Rallying Cry (only coloured box) -->
  {cry_block}

  <!-- On Balance -->
  {balance_block}

  <!-- Sign-off -->
  <tr><td style="padding:8px 0 0;border-top:1px solid {RULE};"></td></tr>
  <tr><td align="center" style="padding:22px 0 6px;font:italic 16px/1.5 {SERIF};color:{TEXT};">{esc(signoff)}</td></tr>

  <!-- Footer -->
  <tr><td align="center" style="padding:24px 0 8px;font:12px/1.6 {FONT};color:{MUTED};">
    You’re receiving this because you subscribed to Bright Spots from Rally News.<br>
    <a href="{{{{ unsubscribe }}}}" style="color:{MUTED};text-decoration:underline;">Unsubscribe</a>
    &nbsp;&middot;&nbsp; <a href="{HOME_URL}" style="color:{MUTED};text-decoration:underline;">rally.news</a>
  </td></tr>

</table></td></tr></table>
</body></html>"""


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
    news = get_json(NEWS_URL, limit=60)
    featured, more = pick_articles(news)
    if not featured:
        print("No articles available — aborting.", file=sys.stderr)
        sys.exit(1)

    url_to_article = {a.get("url"): a for a in news if a.get("url")}

    try:
        cry = (get_json(RALLYING_URL, limit=1) or [None])[0]
    except Exception as e:
        print(f"[warn] rallying cry fetch failed: {e}", file=sys.stderr); cry = None
    try:
        balance = (get_json(BALANCE_URL, limit=1) or [None])[0]
    except Exception as e:
        print(f"[warn] balance fetch failed: {e}", file=sys.stderr); balance = None

    intro = make_intro([featured] + more)
    labels = make_labels(more)
    signoff = make_signoff()

    subject = build_subject(featured)
    html_out = build_html(featured, more, labels, intro, cry, balance, signoff, url_to_article)

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
