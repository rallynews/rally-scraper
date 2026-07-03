#!/usr/bin/env python3
"""
Rallying Cries — newsletter compiler & sender for Rally News.

Philosophy: AI-compiled, NOT AI-generated. Real, human-written journalism that
the scraper surfaced is poured into a fixed template. The hero is the scraper's
most recent Rallying Cry paragraph (already written upstream); below it sits one
card per story the cry referenced, each showing the article's real thumbnail and
summary with a "Read More" link. The ONLY copy this script asks an LLM to write
is the short, keywords-based subject-line title.

Data source: the LIVE rally.news API (rallying-cry.php for the cry, news.php to
recover each referenced story's thumbnail, summary, and category for in-site
Reader View links), NOT the committed JSON files in the repo (those are frozen
snapshots).

Send: creates a Brevo "email campaign" and sends it immediately to the
Rallying Cries list.

Required environment variables (GitHub Actions secrets):
  NEWS_API_URL        e.g. https://rally.news/api/news.php  (base is derived from this)
  OPENROUTER_API_KEY  for the AI subject-line title (Mistral Small, same as the scraper)
  BREVO_API_KEY       for sending

Optional:
  RALLYING_LIST_ID    Brevo list id to send to (default 5)
  DRY_RUN=1           build the email and write rallying_cries_preview.html, do NOT send
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

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
DRY_RUN = os.environ.get("DRY_RUN", "").strip() not in ("", "0", "false", "False")

# Brevo
SENDER = {"name": "Rallying Cries", "email": "rallyingcries@rally.news"}
LIST_ID = int(os.environ.get("RALLYING_LIST_ID", "").strip() or "5")  # Rallying Cries

# Brand / design — mirrors Bright Spots so the two emails feel like one family.
RALLY_GREEN = "#5A775E"
TEXT = "#1A1A1A"
MUTED = "#777777"
RULE = "#E5E5E5"
BOX_BG = "#EDF1ED"           # light green-grey, used for the article cards
LOGO_URL = "https://rally.news/images/icons/rallyingcries.jpg"
HOME_URL = "https://rally.news/"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
SERIF = "Georgia,'Times New Roman',serif"

# Dark-mode equivalents (used in the @media CSS block only)
DM_TEXT = "#E8E8E8"
DM_MUTED = "#AAAAAA"
DM_GREEN = "#90C296"         # lighter green, legible on dark backgrounds
DM_BOX   = "#1E3020"         # dark green for the cards
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
                 "Content-Type": "application/json"},
        json={
            "model": OR_MODEL,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system",
                 "content": ("You are a concise editor for a positive-news email brand "
                             "called Rallying Cries from Rally News. Output ONLY what is "
                             "asked — no preamble, no markdown, no quotation marks around "
                             "your answer.")},
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
# Story → card data
# ─────────────────────────────────────────────────────────────────────────────
def card_data(story, url_to_article):
    """Turn one Rallying Cry story ({title, url}) into the data a card needs.

    The cry only stores the title and the original source URL. We match that URL
    against the live news feed to recover the article's thumbnail, summary, and
    category — the category lets us build an in-ecosystem Reader View link
    (preferred); if there is no match we fall back to the original source URL and
    render the card with whatever we have (no image / no summary)."""
    title = clean(story.get("title", ""))
    src = story.get("url", "")
    match = url_to_article.get(src)
    if match:
        href = reader_url(match.get("category", ""), story.get("title", ""))
        image = match.get("image_url") or ""
        summary = clean(match.get("summary") or match.get("content") or "")
    else:
        href = src
        image = ""
        summary = ""
    return {"href": href, "title": title, "image": image, "summary": summary}


def collect_cards(cry, url_to_article):
    """Build the ordered, de-duplicated list of cards for the cry's stories."""
    cards = []
    seen = set()
    for s in (cry.get("stories") or []):
        data = card_data(s, url_to_article)
        if not data["title"]:
            continue
        key = data["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        cards.append(data)
    return cards


# ─────────────────────────────────────────────────────────────────────────────
# AI micro-copy (subject-line title only, with a safe fallback)
# ─────────────────────────────────────────────────────────────────────────────
def make_subject_title(content: str) -> str:
    """A short, keywords-based title summarising the cry paragraph, e.g.
    'World Cup wins, new cancer tech, and more'."""
    try:
        raw = ai(
            "Below is a one-paragraph summary of today's good news. Write a very "
            "short, keywords-based title (about 5–9 words) that lists its main "
            "topics, in the style of these examples:\n"
            "  • World Cup wins, new cancer tech, and more\n"
            "  • Election wins and new progress on climate change\n"
            "  • Art collections, a new eco-friendly AI model, and cheap drugs\n"
            "Use 'and more' only if it reads naturally. Output ONLY the title — no "
            "leading 'Rallying Cry:', no quotation marks.\n\n" + content,
            max_tokens=40, temperature=0.7,
        )
        title = raw.strip().strip('"').strip("“”").strip()
        # Drop a leading "Rallying Cry:" if the model added one anyway.
        title = re.sub(r"^\s*rallying cr(?:y|ies)\s*:\s*", "", title, flags=re.I)
        if title:
            return title
    except Exception as e:
        print(f"[warn] subject title AI failed: {e}", file=sys.stderr)
    return "today's good news, and more"


# ─────────────────────────────────────────────────────────────────────────────
# HTML assembly (pure function — easy to preview/test)
# ─────────────────────────────────────────────────────────────────────────────
# Dark-mode CSS class legend (applied alongside inline styles):
#   dm-t  → main text colour        light: #1A1A1A  dark: #E8E8E8
#   dm-m  → muted text colour       light: #777777  dark: #AAAAAA
#   dm-g  → green accent colour     light: #5A775E  dark: #90C296
#   dm-b  → card background         light: #EDF1ED  dark: #1E3020
#   dm-r  → rule / border colour    light: #E5E5E5  dark: #3A3A3A
def build_card_html(card):
    parts = []
    if card["image"]:
        parts.append(
            f'<a href="{esc(card["href"])}" style="text-decoration:none;">'
            f'<img src="{esc(card["image"])}" width="510" alt="" '
            f'style="display:block;width:100%;max-width:510px;height:auto;border:0;'
            f'border-radius:6px;margin:0 0 14px;"></a>'
        )
    parts.append(
        f'<a href="{esc(card["href"])}" class="dm-t" '
        f'style="font:bold 19px/1.35 {SERIF};color:{TEXT};text-decoration:none;">'
        f'{esc(card["title"])}</a>'
    )
    if card["summary"]:
        parts.append(
            f'<p class="dm-t" style="margin:10px 0 16px;font:15px/1.6 {FONT};color:{TEXT};">'
            f'{esc(card["summary"])}</p>'
        )
    else:
        parts.append('<div style="height:16px;line-height:16px;">&nbsp;</div>')
    parts.append(
        f'<a href="{esc(card["href"])}" class="dm-g" '
        f'style="font:bold 14px/1 {FONT};color:{RALLY_GREEN};text-decoration:none;">'
        f'Read More &rarr;</a>'
    )
    inner = "".join(parts)
    return (
        f'<tr><td style="padding:0 0 18px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'class="dm-b dm-r" style="background:{BOX_BG};border:1px solid {RULE};border-radius:8px;">'
        f'<tr><td style="padding:20px 22px;">{inner}</td></tr>'
        f'</table></td></tr>'
    )


def build_html(content, cards):
    today = datetime.date.today().strftime("%A, %B %-d, %Y")
    cards_html = "".join(build_card_html(c) for c in cards)

    return f"""\
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<title>Rallying Cry</title>
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
    <img src="{LOGO_URL}" alt="Rallying Cries" width="600"
         style="display:block;width:100%;max-width:600px;height:auto;border:0;">
  </td></tr>
  <tr><td align="center" class="dm-m" style="padding:0 0 22px;font:13px/1.4 {FONT};color:{MUTED};letter-spacing:.3px;">{today}</td></tr>

  <!-- Rallying Cry paragraph (the hero) -->
  <tr><td class="dm-t" style="padding:0 0 28px;font:19px/1.6 {SERIF};color:{TEXT};">{esc(clean(content))}</td></tr>

  <!-- Article cards -->
  <tr><td class="dm-r" style="padding:0 0 6px;border-top:1px solid {RULE};"></td></tr>
  <tr><td class="dm-t" style="padding:14px 0 16px;font:bold 18px/1.3 {SERIF};color:{TEXT};">In this Rallying Cry</td></tr>
  <tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{cards_html}</table></td></tr>

  <!-- Continue reading -->
  <tr><td style="padding:6px 0 30px;">
    <a href="{HOME_URL}" class="dm-g" style="font:bold 15px/1 {FONT};color:{RALLY_GREEN};text-decoration:none;">Continue reading good news &rarr;</a>
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
    You&#8217;re receiving this because you subscribed to Rallying Cries from Rally News.<br>
    <a href="{{{{ unsubscribe }}}}" class="dm-m" style="color:{MUTED};text-decoration:underline;">Unsubscribe</a>
    &nbsp;&middot;&nbsp; <a href="{HOME_URL}" class="dm-m" style="color:{MUTED};text-decoration:underline;">rally.news</a>
  </td></tr>

</table></td></tr></table>
</body></html>"""


def build_subject(content: str) -> str:
    return f"Rallying Cry: {make_subject_title(content)}"


# ─────────────────────────────────────────────────────────────────────────────
# Brevo send
# ─────────────────────────────────────────────────────────────────────────────
def send_campaign(subject, html_content):
    headers = {"api-key": BREVO_API_KEY, "Content-Type": "application/json", "accept": "application/json"}
    create = requests.post(
        "https://api.brevo.com/v3/emailCampaigns",
        headers=headers,
        json={
            "name": f"Rallying Cries {datetime.date.today().isoformat()}",
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
    cry = parse_api_entry((get_json(RALLYING_URL, limit=1) or [None])[0])
    if not cry or not clean(cry.get("content", "")):
        print("No rallying cry available — aborting.", file=sys.stderr)
        sys.exit(1)

    # A generous news pull so we can recover thumbnails/summaries for the cry's
    # referenced stories (they are recent, but pull wide to maximise matches).
    try:
        news = get_json(NEWS_URL, limit=200)
    except Exception as e:
        print(f"[warn] news fetch failed: {e}", file=sys.stderr); news = []
    url_to_article = {a.get("url"): a for a in news if a.get("url")}

    cards = collect_cards(cry, url_to_article)
    if not cards:
        print("[warn] rallying cry referenced no usable stories — sending paragraph only.",
              file=sys.stderr)

    subject = build_subject(cry["content"])
    html_out = build_html(cry["content"], cards)

    if DRY_RUN:
        with open("rallying_cries_preview.html", "w", encoding="utf-8") as f:
            f.write(html_out)
        print(f"DRY RUN — wrote rallying_cries_preview.html ({len(html_out)} bytes)")
        print(f"Subject: {subject}")
        print(f"Cards: {len(cards)}")
        return

    campaign_id = send_campaign(subject, html_out)
    print(f"Sent Rallying Cries campaign {campaign_id} to list {LIST_ID}.")


if __name__ == "__main__":
    main()
