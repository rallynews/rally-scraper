#!/usr/bin/env python3
"""Default featured images for articles with a broken or missing photo.

Rally keeps a library of royalty-free photos in a public Cloudflare R2 bucket.
When an article has no usable image — nothing in the feed, nothing on the page,
or a URL that turns out to be dead — we pick the library photo whose *file name*
is closest to what the story is about, so a piece on coral reefs gets the reef
photo rather than an arbitrary one.

The list of file names lives in ``fallback_images.json`` next to this file.
R2's public ``r2.dev`` domain serves objects but does not reliably list them, so
the manifest is the source of truth; ``refresh_library()`` tries to rebuild it
from the bucket and ``import_listing()`` builds it from a pasted directory
listing (``rclone lsf``, ``aws s3 ls``, the Cloudflare dashboard, ...).

Run directly to manage the manifest:

    python image_library.py --refresh              # ask the bucket (see R2_* below)
    python image_library.py --import listing.txt   # from a pasted listing ('-' = stdin)
    python image_library.py --match "headline"     # see what a story would get

``--refresh`` uses the S3 API when R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY and R2_BUCKET are set, and otherwise falls back to trying
the public URL, which usually cannot be listed.
"""

import datetime
import difflib
import hashlib
import hmac
import json
import os
import re
import sys
from urllib.parse import quote, unquote

import requests

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Public R2 bucket holding the royalty-free photo library.
FALLBACK_IMAGE_BASE_URL = os.environ.get(
    'FALLBACK_IMAGE_BASE_URL',
    'https://pub-5a350bcb921f42f6a4eb886579d2beb7.r2.dev'
).rstrip('/')

# Subfolder inside the bucket the photos live in.
FALLBACK_IMAGE_PREFIX = os.environ.get('FALLBACK_IMAGE_PREFIX', 'videos').strip('/')

MANIFEST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fallback_images.json')

IMAGE_EXTENSIONS = ('jpg', 'jpeg', 'png', 'webp', 'avif', 'gif')

REQUEST_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; RallyNewsBot/1.0)'}

# ═══════════════════════════════════════════════════════════════
# TEXT HELPERS
# ═══════════════════════════════════════════════════════════════

# Words that carry no subject matter and would otherwise match everything.
_STOPWORDS = {
    'the', 'and', 'for', 'with', 'from', 'that', 'this', 'their', 'they', 'them',
    'has', 'have', 'had', 'was', 'were', 'are', 'been', 'being', 'but', 'not',
    'you', 'your', 'its', 'his', 'her', 'she', 'him', 'who', 'how', 'why', 'what',
    'when', 'where', 'which', 'into', 'onto', 'over', 'under', 'after', 'before',
    'more', 'most', 'less', 'least', 'than', 'then', 'also', 'just', 'new', 'news',
    'says', 'said', 'say', 'will', 'can', 'could', 'would', 'should', 'may',
    'about', 'against', 'between', 'through', 'during', 'while', 'because',
    'first', 'last', 'next', 'one', 'two', 'three', 'many', 'much', 'some', 'any',
    'all', 'out', 'off', 'now', 'day', 'days', 'year', 'years', 'week', 'month',
    'time', 'times', 'way', 'per', 'via', 'amid', 'plan', 'plans', 'set', 'get',
}

# Boilerplate that stock-photo file names are full of; ignoring it keeps the
# match on the actual subject ("pexels-photo-coral-reef" → "coral reef").
_FILENAME_NOISE = {
    'photo', 'photos', 'image', 'images', 'img', 'pic', 'pics', 'picture',
    'pictures', 'stock', 'free', 'royalty', 'video', 'videos', 'clip', 'footage',
    'pexels', 'unsplash', 'pixabay', 'shutterstock', 'copy', 'final', 'edit',
    'edited', 'crop', 'cropped', 'small', 'large', 'medium', 'thumb', 'thumbnail',
    'dsc', 'dscn', 'screenshot', 'untitled', 'default', 'file', 'download',
    'jpg', 'jpeg', 'png', 'webp', 'avif', 'gif', 'hd', 'hq', 'raw', 'scaled',
}

_CAMEL_BOUNDARY = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')


def _stem(word):
    """Very small suffix stripper so 'protests' matches 'protest'."""
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if len(word) > 5 and word.endswith(('ches', 'shes', 'sses', 'xes')):
        return word[:-2]
    if len(word) > 4 and word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    if len(word) > 6 and word.endswith('ing'):
        return word[:-3]
    return word


def _tokenize(text, drop=_STOPWORDS):
    """Split free text into meaningful, stemmed, lowercase tokens."""
    if not text:
        return []
    text = _CAMEL_BOUNDARY.sub(' ', str(text))
    tokens = []
    for raw in re.split(r'[^A-Za-z0-9]+', text.lower()):
        if len(raw) < 3 or raw.isdigit() or raw in drop:
            continue
        stem = _stem(raw)
        if len(stem) >= 3 and stem not in drop:
            tokens.append(stem)
    return tokens


def filename_tokens(filename):
    """Subject tokens for a library file name, with stock boilerplate removed."""
    name = os.path.basename(str(filename))
    name = re.sub(r'\.[A-Za-z0-9]{2,5}$', '', name)          # drop the extension
    tokens = _tokenize(name, drop=_STOPWORDS | _FILENAME_NOISE)
    if not tokens:
        # Nothing but boilerplate — keep the raw words so the file is still
        # reachable rather than silently unmatchable.
        tokens = _tokenize(name)
    return tokens


# Each article category implies a visual subject; these expand a one-word
# category into terms that actually appear in photo file names.
CATEGORY_TERMS = {
    'climate': ['climate', 'environment', 'nature', 'forest', 'tree', 'ocean', 'sea',
                'solar', 'wind', 'energy', 'green', 'earth', 'wildlife', 'river',
                'mountain', 'farm', 'garden', 'recycling'],
    'transportation': ['transport', 'train', 'rail', 'railway', 'bus', 'bike', 'bicycle',
                       'road', 'traffic', 'subway', 'metro', 'tram', 'car', 'airport',
                       'bridge', 'highway', 'ferry', 'harbour'],
    'ai': ['technology', 'computer', 'laptop', 'robot', 'code', 'coding', 'data',
           'science', 'laboratory', 'research', 'space', 'digital', 'circuit',
           'server', 'engineer', 'microscope'],
    'business': ['business', 'office', 'money', 'finance', 'market', 'work',
                 'worker', 'startup', 'shop', 'trade', 'meeting', 'chart',
                 'factory', 'construction', 'coin'],
    'politics': ['politics', 'government', 'parliament', 'flag', 'vote', 'voting',
                 'ballot',
                 'election', 'democracy', 'law', 'court', 'justice', 'capitol',
                 'protest', 'march', 'city'],
    'entertainment': ['music', 'concert', 'film', 'movie', 'cinema', 'sport', 'game',
                      'stage', 'festival', 'dance', 'guitar', 'stadium', 'crowd',
                      'camera', 'party'],
    'world': ['world', 'globe', 'map', 'city', 'people', 'community', 'travel',
              'flag', 'street', 'village', 'crowd', 'family', 'peace'],
    'religion': ['church', 'temple', 'mosque', 'faith', 'prayer', 'candle',
                 'spiritual', 'monastery', 'cathedral', 'ritual'],
    'arts': ['art', 'artist', 'museum', 'book', 'library', 'paint', 'painting',
             'theatre', 'theater', 'gallery', 'sculpture', 'craft', 'pottery',
             'mural', 'writing'],
}

# ═══════════════════════════════════════════════════════════════
# MANIFEST
# ═══════════════════════════════════════════════════════════════

def load_library(path=MANIFEST_PATH):
    """Return the list of photo file names from the manifest ([] if unusable)."""
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get('images', [])
    if not isinstance(data, list):
        return []
    return [str(n).strip() for n in data if str(n).strip()]


def save_library(names, path=MANIFEST_PATH):
    """Write the manifest, sorted and de-duplicated."""
    names = sorted({str(n).strip() for n in names if str(n).strip()})
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(names, fh, indent=2, ensure_ascii=False)
        fh.write('\n')
    return names


def image_url(filename):
    """Public URL for one library file name."""
    name = quote(str(filename).strip().lstrip('/'), safe='/')
    prefix = f"{FALLBACK_IMAGE_PREFIX}/" if FALLBACK_IMAGE_PREFIX else ''
    return f"{FALLBACK_IMAGE_BASE_URL}/{prefix}{name}"


def is_fallback_url(url):
    """True if the URL already points at the fallback library."""
    return bool(url) and str(url).startswith(FALLBACK_IMAGE_BASE_URL)

# ═══════════════════════════════════════════════════════════════
# DISCOVERY
# ═══════════════════════════════════════════════════════════════

_LISTING_ENTRY = re.compile(
    r'[A-Za-z0-9%][A-Za-z0-9 _%+.()\'\-]*\.(?:' + '|'.join(IMAGE_EXTENSIONS) + r')\b',
    re.IGNORECASE,
)


def parse_listing(text):
    """Pull image file names out of any listing format.

    Handles S3/R2 XML (``<Key>videos/foo.jpg</Key>``), HTML auto-indexes, JSON
    arrays and plain output from ``rclone lsf`` / ``aws s3 ls`` alike — anything
    that mentions the file names is enough.
    """
    names = []
    for match in _LISTING_ENTRY.finditer(text or ''):
        name = unquote(match.group(0)).strip()
        name = os.path.basename(name.replace('\\', '/'))
        if name:
            names.append(name)
    return sorted(set(names))


def _fetch(url, timeout=15):
    try:
        resp = requests.get(url, timeout=timeout, headers=REQUEST_HEADERS)
        if resp.ok:
            return resp.text
    except requests.RequestException:
        pass
    return None


# Optional S3 credentials for the bucket. The public r2.dev domain serves
# objects but will not list them, so a real listing needs the S3 API.
R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID')
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET = os.environ.get('R2_BUCKET')


def _sign_v4(secret, date_stamp, region, service, string_to_sign):
    def hmac_sha256(key, msg):
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

    key = hmac_sha256(f"AWS4{secret}".encode('utf-8'), date_stamp)
    key = hmac_sha256(key, region)
    key = hmac_sha256(key, service)
    key = hmac_sha256(key, 'aws4_request')
    return hmac.new(key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()


def _list_via_s3_api(timeout=20):
    """List the folder through R2's S3 API. Needs R2_* credentials; [] without them."""
    if not (R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET):
        return []

    host = f"{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    region, service = 'auto', 's3'
    prefix = f"{FALLBACK_IMAGE_PREFIX}/" if FALLBACK_IMAGE_PREFIX else ''

    names, token = [], None
    for _ in range(20):  # 20 x 1000 keys is plenty for this library
        params = [('list-type', '2'), ('max-keys', '1000'), ('prefix', prefix)]
        if token:
            params.append(('continuation-token', token))
        query = '&'.join(
            f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(params)
        )

        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date = now.strftime('%Y%m%dT%H%M%SZ')
        date_stamp = now.strftime('%Y%m%d')
        payload_hash = hashlib.sha256(b'').hexdigest()

        canonical = '\n'.join([
            'GET', f"/{R2_BUCKET}", query,
            f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n",
            'host;x-amz-content-sha256;x-amz-date', payload_hash,
        ])
        scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = '\n'.join([
            'AWS4-HMAC-SHA256', amz_date, scope,
            hashlib.sha256(canonical.encode('utf-8')).hexdigest(),
        ])
        signature = _sign_v4(R2_SECRET_ACCESS_KEY, date_stamp, region, service, string_to_sign)

        headers = {
            'Authorization': (
                f"AWS4-HMAC-SHA256 Credential={R2_ACCESS_KEY_ID}/{scope}, "
                f"SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature={signature}"
            ),
            'x-amz-content-sha256': payload_hash,
            'x-amz-date': amz_date,
        }

        try:
            resp = requests.get(f"https://{host}/{R2_BUCKET}?{query}",
                                headers=headers, timeout=timeout)
        except requests.RequestException as e:
            print(f"R2 S3 listing failed: {type(e).__name__}: {e}")
            return []
        if not resp.ok:
            print(f"R2 S3 listing failed: HTTP {resp.status_code} {resp.text[:200]}")
            return []

        names.extend(parse_listing(resp.text))
        match = re.search(r'<NextContinuationToken>([^<]+)</NextContinuationToken>', resp.text)
        truncated = '<IsTruncated>true</IsTruncated>' in resp.text
        if not (truncated and match):
            break
        token = match.group(1)

    return sorted(set(names))


def discover_library():
    """Best-effort listing of the bucket folder. Returns [] if it can't be read.

    The S3 API is tried first (needs R2_* credentials) because r2.dev serves
    objects but generally refuses to list them. Every strategy is optional; a
    failure just means the committed manifest stands.
    """
    names = _list_via_s3_api()
    if names:
        return names

    prefix = f"{FALLBACK_IMAGE_PREFIX}/" if FALLBACK_IMAGE_PREFIX else ''
    candidates = [
        f"{FALLBACK_IMAGE_BASE_URL}/{prefix}index.json",          # manifest uploaded alongside the photos
        f"{FALLBACK_IMAGE_BASE_URL}/?list-type=2&prefix={prefix}",  # S3-style listing
        f"{FALLBACK_IMAGE_BASE_URL}/{prefix}",                     # HTML auto-index
    ]
    for url in candidates:
        body = _fetch(url)
        if not body:
            continue
        names = parse_listing(body)
        if names:
            return names
    return []


def refresh_library(path=MANIFEST_PATH):
    """Re-read the bucket and rewrite the manifest. Returns the names kept."""
    discovered = discover_library()
    if not discovered:
        return load_library(path)
    return save_library(discovered, path)


def import_listing(text, path=MANIFEST_PATH):
    """Rebuild the manifest from a pasted directory listing."""
    names = parse_listing(text)
    if not names:
        return []
    return save_library(names, path)

# ═══════════════════════════════════════════════════════════════
# REACHABILITY
# ═══════════════════════════════════════════════════════════════

_reachable_cache = {}


def is_reachable(url, timeout=8):
    """True if the URL actually serves an image right now.

    A HEAD is tried first; some CDNs reject HEAD, so a ranged GET is the
    fallback. Results are cached for the life of the process.
    """
    if not url or not str(url).startswith(('http://', 'https://')):
        return False
    if url in _reachable_cache:
        return _reachable_cache[url]

    result = False
    for method in ('head', 'get'):
        try:
            headers = dict(REQUEST_HEADERS)
            if method == 'get':
                headers['Range'] = 'bytes=0-1023'
            resp = requests.request(
                method, url, timeout=timeout, headers=headers,
                allow_redirects=True, stream=(method == 'get'),
            )
            if method == 'get':
                resp.close()
            if resp.status_code >= 400:
                if method == 'head' and resp.status_code in (403, 405, 501):
                    continue  # HEAD not allowed — try the ranged GET
                result = False
                break
            content_type = resp.headers.get('Content-Type', '').lower()
            # Some hosts omit or mislabel the type; only reject a clear non-image.
            if content_type and not content_type.startswith('image/'):
                if content_type.startswith(('text/html', 'application/json')):
                    result = False
                    break
            result = True
            break
        except requests.RequestException:
            continue

    _reachable_cache[url] = result
    return result

# ═══════════════════════════════════════════════════════════════
# MATCHING
# ═══════════════════════════════════════════════════════════════

# How much each part of an article counts towards the match.
_WEIGHTS = {
    'topics': 3.5,          # curated subject phrases — the strongest signal
    'title': 3.0,
    'countries': 2.0,
    'category': 1.5,        # the literal category word
    'category_terms': 1.0,  # subjects merely implied by the category
    'summary': 1.0,
}

_FUZZY_THRESHOLD = 0.86   # 'reef' vs 'reefs' style near-misses
_FUZZY_CREDIT = 0.6       # a near-miss is worth this fraction of an exact hit
_PREFIX_CREDIT = 0.8      # 'court' opening 'courtroom'
_CONTAINS_CREDIT = 0.6    # 'school' buried inside 'schoolchildren'


def article_terms(title='', summary='', topics=(), category='', countries=()):
    """Weighted subject terms for an article, strongest signal first."""
    terms = {}

    def add(values, weight):
        for token in values:
            if token and terms.get(token, 0) < weight:
                terms[token] = weight

    add(_tokenize(' '.join(topics or ())), _WEIGHTS['topics'])
    add(_tokenize(title), _WEIGHTS['title'])
    add(_tokenize(' '.join(countries or ())), _WEIGHTS['countries'])
    if category:
        add(_tokenize(category), _WEIGHTS['category'])
        add([_stem(t) for t in CATEGORY_TERMS.get(str(category).lower(), [])],
            _WEIGHTS['category_terms'])
    add(_tokenize(summary), _WEIGHTS['summary'])
    return terms


def score_filename(filename, terms):
    """How well one library file name matches an article's weighted terms."""
    tokens = filename_tokens(filename)
    if not tokens or not terms:
        return 0.0

    total = 0.0
    for token in tokens:
        if token in terms:
            total += terms[token]
            continue
        best = 0.0
        for term, weight in terms.items():
            # Compound file-name words: 'court' inside 'courtroom-justice'.
            if len(term) >= 4 and len(token) >= 4:
                if token.startswith(term) or term.startswith(token):
                    best = max(best, weight * _PREFIX_CREDIT)
                elif term in token or token in term:
                    best = max(best, weight * _CONTAINS_CREDIT)
            credit = weight * _FUZZY_CREDIT
            if credit > best and difflib.SequenceMatcher(None, token, term).ratio() >= _FUZZY_THRESHOLD:
                best = credit
        total += best

    # Divide by the token count (softened) so a long file name can't win on
    # sheer length alone.
    return total / (len(tokens) ** 0.5)


def pick_image(title='', summary='', topics=(), category='', countries=(),
               used_images=(), library=None):
    """Best-matching library photo for an article. Returns a URL, or None.

    Photos in `used_images` are avoided; the caller decides that scope. The
    scraper passes the photos already used that day, so a photo is free to come
    round again on another day. If everything is taken the closest match is
    reused rather than leaving the article imageless.
    """
    names = list(library) if library is not None else load_library()
    if not names:
        return None

    used = {u for u in (used_images or ()) if u}
    terms = article_terms(title, summary, topics, category, countries)

    scored = sorted(
        ((score_filename(name, terms), name) for name in names),
        key=lambda pair: (-pair[0], pair[1]),
    )

    # Nothing in the library relates to the story: spread the generic photos out
    # deterministically instead of handing every such article the same one.
    if scored[0][0] <= 0:
        digest = hashlib.md5(f"{title}|{category}".encode('utf-8')).hexdigest()
        seed = int(digest[:8], 16)
        rotated = [name for _, name in scored]
        scored = [(0.0, rotated[(seed + i) % len(rotated)]) for i in range(len(rotated))]

    for _, name in scored:
        url = image_url(name)
        if url not in used:
            return url

    return image_url(scored[0][1])

# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def _main(argv):
    if '--refresh' in argv:
        names = refresh_library()
        print(f"Manifest now holds {len(names)} photos ({MANIFEST_PATH})")
        if not names:
            print("The bucket could not be listed — import a listing instead:")
            print("  python image_library.py --import listing.txt")
        return 0

    if '--import' in argv:
        idx = argv.index('--import')
        source = argv[idx + 1] if len(argv) > idx + 1 else '-'
        text = sys.stdin.read() if source == '-' else open(source, encoding='utf-8').read()
        names = import_listing(text)
        print(f"Imported {len(names)} photos into {MANIFEST_PATH}")
        return 0 if names else 1

    if '--match' in argv:
        idx = argv.index('--match')
        headline = argv[idx + 1] if len(argv) > idx + 1 else ''
        names = load_library()
        if not names:
            print("Manifest is empty — run --refresh or --import first.")
            return 1
        terms = article_terms(title=headline)
        ranked = sorted(((score_filename(n, terms), n) for n in names), reverse=True)
        for score, name in ranked[:10]:
            print(f"  {score:6.2f}  {name}")
        print(f"\n→ {pick_image(title=headline)}")
        return 0

    print(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
