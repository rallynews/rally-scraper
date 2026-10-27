"""Joyscroll's Joy clips: find short, joyful videos people have posted publicly
on Bluesky, for a person to review in Studio ▸ News ▸ Joy clips.

    python bluesky_clips.py               # find: search, filter, check, post candidates
    python bluesky_clips.py --dry-run     # the same, printing instead of posting
    python bluesky_clips.py --recheck     # look again at every approved/live clip

Dispatched by cron-job.org: finding every 6 hours, re-checking daily
(.github/workflows/bluesky-clips.yml). This only proposes clips: whether one
goes live at once or waits for a person is Studio's review setting, decided
by api/bluesky-clips.php, and creators can opt out at rally.news/joyscroll/creators.

The rules this follows (JOYSCROLL_BLUESKY_CLIPS_BRIEF.md §0):

- **Read only.** It signs in once per run to its own account with an app
  password and only ever reads: no posts, likes, follows, replies or messages.
- **Nothing copied.** Post text and thumbnails are held in memory for the
  checks and dropped. What is sent to Rally is the post's AT-URI, CID and
  rkey, the author's DID, and verdicts in our own words — never the text, a
  handle, a name, an avatar or any media URL.
- **Every label honoured.** Labels on the post and on the author's profile,
  self-labels and Bluesky's moderation service both, keep a clip out.
- **Within the limits.** At least a second between requests, backing off on
  429 and stopping after three in a row or any 401/403; hard caps per run.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

BLUESKY_HANDLE = os.environ.get('BLUESKY_HANDLE')
BLUESKY_APP_PASSWORD = os.environ.get('BLUESKY_APP_PASSWORD')
CLIPS_API_KEY = os.environ.get('JOYSCROLL_CLIPS_API_KEY')
NEWS_API_URL = os.environ.get('NEWS_API_URL')
CLIPS_API_URL = f"{NEWS_API_URL.rsplit('/', 1)[0]}/bluesky-clips.php" if NEWS_API_URL else None
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
TEXT_MODEL = os.environ.get('CLIPS_TEXT_MODEL') or 'openai/gpt-4o-mini'
VISION_MODEL = os.environ.get('CLIPS_VISION_MODEL') or 'openai/gpt-4o-mini'
CONTACT = os.environ.get('CLIPS_CONTACT_EMAIL') or 'joyscroll@rally.news'

# The same attribution scraper.py sends: https://openrouter.ai/docs/app-attribution
OPENROUTER_ATTRIBUTION = {'HTTP-Referer': 'https://rally.news', 'X-OpenRouter-Title': 'Rally'}
USER_AGENT = f'RallyJoyscrollClips/1.0 (+https://rally.news/joyscroll/creators; {CONTACT})'

# Bluesky's own moderation labeler — @atproto/api's BSKY_LABELER_DID
# (packages/api/src/const.ts), checked 2026-10-26.
BSKY_LABELER_DID = 'did:plc:ar7c4by46qjdydhdevvrndac'

# Hard caps per run (brief §3.1).
MAX_SEARCHES = 20
MAX_EXAMINED = 1000
MAX_CANDIDATES = 40
MAX_MODEL_CALLS = 60
PAGES_PER_QUERY = 2
SINCE_HOURS = 48
# Bluesky allows 3,000 requests per 5 minutes per IP; one a second is a tenth.
MIN_INTERVAL_S = 1.0
JOY_MIN = 7

CATEGORIES = ['animals', 'nature', 'kindness', 'community', 'achievement', 'creativity',
              'family-friendly fun', 'food', 'sport']

# Labels that keep a clip out (brief §3.3 step 4).
EXCLUDE_LABELS = {
    '!no-unauthenticated', '!hide', '!warn',
    'porn', 'sexual', 'nudity', 'graphic-media', 'gore',
    'spam', 'impersonation', 'misleading', 'rude', 'intolerant', 'threat', 'self-harm',
}

TEXT_FLAGS = ['political', 'news_claim', 'advertising_or_selling', 'fundraising', 'medical_or_financial_claim',
              'distress_or_danger', 'children_focus', 'likely_not_original']
THUMB_FLAGS = ['watermark_other_platform', 'tv_or_film_footage', 'child_is_main_subject', 'person_in_distress',
               'nudity_or_violence', 'text_overlay_contradicts_post']

# Skipped without a trace: not a clip at all, seen before, or a creator who
# has opted out — none of these is a finding about the post.
QUIET_SKIPS = {'not a video', 'already seen', 'creator on the do-not-feature list'}

VIDEO_VIEW = 'app.bsky.embed.video#view'
RECORD_WITH_MEDIA_VIEW = 'app.bsky.embed.recordWithMedia#view'


# ═══════════════════════════════════════════════════════════════
# THE CHECKS (pure: plain dicts in, a reason or None out)
# ═══════════════════════════════════════════════════════════════

def video_view(post):
    """The post's video, or None: a video embed, or a quote-with-media whose media is a video."""
    e = post.get('embed') or {}
    if e.get('$type') == VIDEO_VIEW:
        return e
    if e.get('$type') == RECORD_WITH_MEDIA_VIEW and (e.get('media') or {}).get('$type') == VIDEO_VIEW:
        return e['media']
    return None


def label_values(labels):
    return {str(l.get('val', '')) for l in labels or [] if isinstance(l, dict)}


def excluded_label(post, profile=None):
    """The first reason a label keeps this clip out, or None.

    Post labels are checked against the exclusion list. On the creator's
    profile, any label at all keeps them out: Bluesky's moderation labels
    on an account all say to hide or blur it, and a self-labelled account
    (bot, adult) isn't one Joyscroll should feature either."""
    for v in sorted(label_values(post.get('labels'))):
        if v in EXCLUDE_LABELS:
            return f'label: {v}'
    author = (profile or {}).get('labels') or (post.get('author') or {}).get('labels')
    for v in sorted(label_values(author)):
        if v == '!no-unauthenticated':
            return 'creator hides from logged-out viewers'
        return f'creator label: {v}'
    return None


def hard_filter(post, known_uris, blocked_dids):
    """Code-only checks (brief §3.3, except the profile, checked later in a
    batch). Returns (reason, store): the reason it fails or None, and whether a
    failure is worth remembering so the post is never examined again."""
    if not video_view(post):
        return 'not a video', False
    uri = post.get('uri', '')
    if uri in known_uris:
        return 'already seen', False
    author = (post.get('author') or {}).get('did', '')
    if author in blocked_dids:
        return 'creator on the do-not-feature list', False
    record = post.get('record') or {}
    if record.get('reply'):
        return 'a reply', True
    e = post.get('embed') or {}
    if e.get('$type') == RECORD_WITH_MEDIA_VIEW:
        quoted = ((e.get('record') or {}).get('record') or {})
        if ((quoted.get('author') or {}).get('did')) != author:
            return 'quotes someone else', True
    label = excluded_label({'labels': post.get('labels')})
    if label:
        return label, True
    langs = [str(l).lower() for l in record.get('langs') or []]
    if not any(l == 'en' or l.startswith('en-') for l in langs):
        return 'not English', True
    ratio = video_view(post).get('aspectRatio') or {}
    if ratio.get('width') and ratio.get('height') and ratio['width'] > ratio['height']:
        return 'landscape', True
    return None, False


def judge_text(v):
    """The text check's verdict: (fields to keep, reason it fails or None).
    A reply that isn't well-formed is (None, None): no verdict, try again later."""
    if not isinstance(v, dict) or not isinstance(v.get('joy_score'), (int, float)):
        return None, None
    score = max(0, min(10, int(v['joy_score'])))
    category = v.get('category') if v.get('category') in CATEGORIES else None
    topics = [str(t).strip().lower()[:30] for t in (v.get('topics') or []) if str(t).strip()][:3]
    fields = {'joy_score': score, 'category': category, 'topics': topics,
              'check_notes': str(v.get('notes') or '').strip()[:300] or None}
    if score < JOY_MIN:
        return fields, f'text: joy below {JOY_MIN}'
    for flag in TEXT_FLAGS:
        if v.get(flag) is True:
            return fields, f'text: {flag}'
    if not category:
        return fields, 'text: no category'
    return fields, None


def judge_thumbnail(v):
    """The thumbnail check's reason to reject, None to pass, or '' for no verdict."""
    if not isinstance(v, dict) or not all(isinstance(v.get(f), bool) for f in THUMB_FLAGS):
        return ''
    for flag in THUMB_FLAGS:
        if v[flag]:
            return f'thumbnail: {flag}'
    return None


def recheck_verdict(clip, post, profile=None):
    """What the daily re-check finds (brief §3.5): ok, gone, changed or relabelled."""
    if not post:
        return 'gone'
    if post.get('cid') != clip['cid']:
        return 'changed'
    if excluded_label(post, profile):
        return 'relabelled'
    return 'ok'


# ═══════════════════════════════════════════════════════════════
# BLUESKY (read only)
# ═══════════════════════════════════════════════════════════════

class StopRun(Exception):
    """Bluesky said stop: three 429s in a row, or a 401/403. The run ends cleanly."""


class Bluesky:
    """One session per run, every call spaced and backed off."""

    def __init__(self):
        from atproto import Client
        from atproto_client.models.utils import get_model_as_dict
        self._as_dict = get_model_as_dict
        self.client = Client('https://bsky.social')
        self.client.request.add_additional_header('User-Agent', USER_AGENT)
        # Ask the AppView to attach Bluesky's moderation labels as well as self-labels.
        self.client.request.add_additional_header('atproto-accept-labelers', BSKY_LABELER_DID)
        self.calls = 0
        self._last = 0.0
        self._429s = 0

    def login(self):
        # Once per run: the login limit is far lower than anything else.
        self._call(lambda: self.client.login(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD))

    def _call(self, fn):
        from atproto_client import exceptions as ex
        delay = 2.0
        while True:
            wait = MIN_INTERVAL_S - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.calls += 1
            try:
                out = fn()
                self._429s = 0
                return out
            except ex.RateLimitExceededError as e:
                self._429s += 1
                if self._429s >= 3:
                    raise StopRun('three 429s in a row')
                headers = getattr(getattr(e, 'response', None), 'headers', {}) or {}
                retry = headers.get('retry-after') or headers.get('Retry-After')
                time.sleep(max(delay, float(retry)) if retry and str(retry).isdigit() else delay)
                delay *= 2
            except ex.UnauthorizedError as e:
                raise StopRun(f'Bluesky refused the request ({e.__class__.__name__})')

    def search(self, q, since, cursor=None):
        params = {'q': q, 'lang': 'en', 'sort': 'top', 'since': since, 'limit': 100}
        if cursor:
            params['cursor'] = cursor
        r = self._call(lambda: self.client.app.bsky.feed.search_posts(params))
        return [self._as_dict(p) for p in r.posts], r.cursor

    def profiles(self, dids):
        out = {}
        for i in range(0, len(dids), 25):
            r = self._call(lambda: self.client.app.bsky.actor.get_profiles({'actors': dids[i:i + 25]}))
            for p in r.profiles:
                d = self._as_dict(p)
                out[d['did']] = d
        return out

    def posts(self, uris):
        out = {}
        for i in range(0, len(uris), 25):
            r = self._call(lambda: self.client.app.bsky.feed.get_posts({'uris': uris[i:i + 25]}))
            for p in r.posts:
                d = self._as_dict(p)
                out[d['uri']] = d
        return out


# ═══════════════════════════════════════════════════════════════
# MODEL CHECKS (in memory only)
# ═══════════════════════════════════════════════════════════════

class Budget:
    def __init__(self, max_calls=MAX_MODEL_CALLS):
        self.max_calls, self.calls, self.cost = max_calls, 0, 0.0

    def can_call(self, n=1):
        return self.calls + n <= self.max_calls


def call_model(model, content, budget, max_tokens=400):
    """One OpenRouter call that must answer with a JSON object, or None."""
    if not OPENROUTER_API_KEY or not budget.can_call():
        return None
    budget.calls += 1
    try:
        r = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {OPENROUTER_API_KEY}', 'Content-Type': 'application/json',
                     **OPENROUTER_ATTRIBUTION},
            json={'model': model, 'messages': [{'role': 'user', 'content': content}], 'max_tokens': max_tokens,
                  'temperature': 0, 'response_format': {'type': 'json_object'}, 'usage': {'include': True}},
            timeout=60,
        )
        if r.status_code != 200:
            print(f"      ✗ {model}: {r.status_code}")
            return None
        data = r.json()
        budget.cost += float((data.get('usage') or {}).get('cost') or 0)
        text = data['choices'][0]['message']['content'] or ''
        start, end = text.find('{'), text.rfind('}')
        return json.loads(text[start:end + 1]) if start >= 0 and end > start else None
    except Exception as e:
        print(f"      ✗ {model}: {type(e).__name__}")
        return None


def text_prompt(text, alt):
    return f"""You screen short videos for Joyscroll, a good-news app, from a public social post. Read the post's words and the video's description (you can't see the video) and judge it.

Post: {json.dumps(text or '')}
Video description (alt text): {json.dumps(alt or '')}

Reply with one JSON object:
{{"joy_score": 0-10 (how clearly joyful, warm or uplifting; 7+ means a viewer would smile),
 "category": one of {json.dumps(CATEGORIES)},
 "topics": up to 3 single lowercase words,
 "political": bool, "news_claim": bool, "advertising_or_selling": bool, "fundraising": bool,
 "medical_or_financial_claim": bool, "distress_or_danger": bool, "children_focus": bool,
 "likely_not_original": bool (mentions TikTok, Instagram, TV, "via", "credit to", or reads like a repost of someone else's video),
 "notes": one short line in your own words describing what the clip shows — do not quote the post}}"""


THUMB_PROMPT = """This is the thumbnail of a short video someone posted. Answer with one JSON object of booleans:
{"watermark_other_platform": a TikTok, Instagram, YouTube or other platform's watermark or username overlay,
 "tv_or_film_footage": it looks like TV, film or broadcast footage,
 "child_is_main_subject": a child is the main subject,
 "person_in_distress": someone appears hurt, scared or in danger,
 "nudity_or_violence": any nudity or violence,
 "text_overlay_contradicts_post": text in the image that seems unrelated to a joyful clip}"""


# ═══════════════════════════════════════════════════════════════
# RALLY'S API
# ═══════════════════════════════════════════════════════════════

def api(method, params=None, payload=None, dry_run=False):
    if dry_run and method == 'POST':
        print(f"      (dry run) {payload.get('action')} → {json.dumps({k: v for k, v in payload.items() if k != 'action'})[:300]}")
        return {'success': True}
    r = requests.request(method, CLIPS_API_URL, params=params, json=payload,
                         headers={'X-Api-Key': CLIPS_API_KEY, 'Content-Type': 'application/json'}, timeout=30)
    r.raise_for_status()
    return r.json()


def identifiers(post):
    uri = post['uri']
    return {'at_uri': uri, 'cid': post['cid'], 'author_did': post['author']['did'], 'rkey': uri.rsplit('/', 1)[-1]}


# ═══════════════════════════════════════════════════════════════
# THE RUNS
# ═══════════════════════════════════════════════════════════════

def missing_config(recheck=False):
    need = [('BLUESKY_HANDLE', BLUESKY_HANDLE), ('BLUESKY_APP_PASSWORD', BLUESKY_APP_PASSWORD),
            ('JOYSCROLL_CLIPS_API_KEY', CLIPS_API_KEY), ('NEWS_API_URL', NEWS_API_URL)]
    if not recheck:
        need.append(('OPENROUTER_API_KEY', OPENROUTER_API_KEY))
    return [n for n, v in need if not v]


def find(dry_run=False, bsky=None, now=None):
    """Search, filter, check, and post what was found."""
    known = api('GET', params={'action': 'known'})
    known_uris, blocked = set(known.get('uris') or []), set(known.get('dids') or [])
    queries = (known.get('queries') or [])[:MAX_SEARCHES]
    since = ((now or datetime.now(timezone.utc)) - timedelta(hours=SINCE_HOURS)).strftime('%Y-%m-%dT%H:%M:%SZ')
    bsky = bsky or Bluesky()
    budget = Budget()
    stats = {'searches': 0, 'examined': 0, 'videos': 0, 'candidates': 0, 'rejected': 0}
    items, query_found, stopped = [], [], None

    try:
        bsky.login()
        pool = []  # (post, query) for video posts that passed the code checks
        for q in queries:
            if stats['examined'] >= MAX_EXAMINED:
                break
            cursor, found = None, 0
            for _ in range(PAGES_PER_QUERY):
                posts, cursor = bsky.search(q['query'], since, cursor)
                stats['searches'] += 1
                for post in posts:
                    if stats['examined'] >= MAX_EXAMINED:
                        break
                    stats['examined'] += 1
                    reason, store = hard_filter(post, known_uris, blocked)
                    if reason in QUIET_SKIPS:
                        continue
                    stats['videos'] += 1
                    known_uris.add(post['uri'])
                    found += 1
                    if reason:
                        if store:
                            items.append({**identifiers(post), 'status': 'rejected', 'reject_reason': reason,
                                          'found_by_query': q['query']})
                        continue
                    pool.append((post, q['query']))
                if not cursor:
                    break
            query_found.append({'id': q['id'], 'found': found})

        # Profiles, in batches: a creator who hides from logged-out viewers, or
        # whose account carries any label, is out.
        profiles = bsky.profiles(sorted({p['author']['did'] for p, _ in pool})) if pool else {}
        for post, query in pool:
            ids = identifiers(post)
            reason = excluded_label({'labels': []}, profiles.get(ids['author_did']) or {'labels': []})
            if reason:
                items.append({**ids, 'status': 'rejected', 'reject_reason': reason, 'found_by_query': query})
                continue
            if stats['candidates'] >= MAX_CANDIDATES or not budget.can_call(2):
                break  # left unrecorded: the next run looks again
            video = video_view(post)
            text = (post.get('record') or {}).get('text') or ''
            fields, reason = judge_text(call_model(TEXT_MODEL, text_prompt(text, video.get('alt')), budget))
            if fields is None:
                continue  # no verdict: try again next run
            if not reason and video.get('thumbnail'):
                thumb = judge_thumbnail(call_model(VISION_MODEL, [
                    {'type': 'text', 'text': THUMB_PROMPT},
                    {'type': 'image_url', 'image_url': {'url': video['thumbnail']}},
                ], budget, 200))
                if thumb == '':
                    continue
                reason = thumb
            row = {**ids, **fields, 'found_by_query': query}
            if reason:
                items.append({**row, 'status': 'rejected', 'reject_reason': reason})
            else:
                items.append({**row, 'status': 'pending'})
                stats['candidates'] += 1
            # The post's words and picture go no further than this loop: only
            # identifiers and the verdicts above are sent to Rally.
    except StopRun as e:
        # Bluesky said stop: no more requests of any kind, the creator check
        # included. What was fully checked is kept; the rest is found again
        # next run.
        stopped = str(e)

    stats['rejected'] = sum(1 for i in items if i['status'] == 'rejected')
    if items or query_found:
        api('POST', payload={'action': 'candidates', 'items': items, 'queries': query_found}, dry_run=dry_run)
    print(f"\n  Joy clips: {stats['searches']} searches, {stats['examined']} posts examined, {stats['videos']} new videos, "
          f"{stats['candidates']} for review, {stats['rejected']} turned down; {bsky.calls} Bluesky requests, "
          f"{budget.calls} model calls (cap {MAX_MODEL_CALLS}), ${budget.cost:.4f}"
          + (f" — stopped early: {stopped}" if stopped else ''))
    return stats


def recheck(dry_run=False, bsky=None):
    """Look again at every approved, waiting or live clip (brief §3.5)."""
    clips = api('GET', params={'action': 'recheck'}).get('clips') or []
    bsky = bsky or Bluesky()
    results, stopped = [], None
    try:
        if clips:
            bsky.login()
            posts = bsky.posts([c['at_uri'] for c in clips])
            profiles = bsky.profiles(sorted({p['author']['did'] for p in posts.values()})) if posts else {}
            for c in clips:
                post = posts.get(c['at_uri'])
                verdict = recheck_verdict(c, post, profiles.get(c['author_did']) if post else None)
                r = {'id': c['id'], 'result': verdict}
                if verdict == 'changed':
                    r['cid'] = post['cid']
                if verdict == 'relabelled':
                    r['note'] = excluded_label(post, profiles.get(c['author_did']))
                results.append(r)
    except StopRun as e:
        stopped = str(e)
    if results:
        api('POST', payload={'action': 'recheck_result', 'results': results}, dry_run=dry_run)
    counts = {k: sum(1 for r in results if r['result'] == k) for k in ('ok', 'gone', 'changed', 'relabelled')}
    print(f"\n  Joy clips re-check: {len(results)} of {len(clips)} checked — {counts['ok']} fine, {counts['gone']} gone, "
          f"{counts['changed']} changed, {counts['relabelled']} relabelled; {bsky.calls} Bluesky requests"
          + (f" — stopped early: {stopped}" if stopped else ''))
    return counts


def main(argv):
    mode_recheck = '--recheck' in argv
    missing = missing_config(mode_recheck)
    if missing:
        print(f"Joy clips: skipped — {', '.join(missing)} not set")
        return 0
    (recheck if mode_recheck else find)(dry_run='--dry-run' in argv)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
