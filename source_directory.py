#!/usr/bin/env python3
"""The list of sources Rally scrapes, read from the Rally Admin dashboard.

The master list used to live in this repository, as the WHITELISTED_SOURCES /
RSS_FEEDS / SOURCE_CONTINENTS maps in ``scraper.py``. It is moving to a table
the dashboard edits, so a source can be added without a commit and a deploy.

This module is the read side of that move. It is deliberately paranoid, because
a feed URL is not ordinary configuration: it is a URL this process fetches from
a CI runner, so anything that can change the list can aim network requests.

    remote  --HTTPS + key-->  validate  -->  compare to lock  -->  use
                                 |                 |
                          reject unsafe     implausible? keep the lock

Three things guard it:

* **Every URL is re-validated here**, not just when an admin typed it in. The
  dashboard checks a URL on save, but DNS can change afterwards, and this is the
  process that actually dereferences it. Redirects are followed one hop at a
  time and re-checked, which the dashboard cannot do at all.

* **A committed lockfile** (``sources.lock.json``) is the fallback. If the API
  is unreachable, or answers with something implausible, the scraper uses the
  last known good list rather than scraping nothing — or scraping something
  unexpected. The workflow commits the lockfile, so every change to the list
  leaves a diff in git history: the audit trail that moving off code would
  otherwise cost.

* **A built-in last resort.** If the API is unreachable and there is no
  lockfile either, ``scraper.py`` falls back to the source maps still defined
  at the top of that file. A scraper with no sources at all is worse than one
  running a slightly stale list.

Run directly to see what the API is serving and how it differs from the code:

    python source_directory.py --check         # fetch, validate, diff, exit
    python source_directory.py --verify        # fetch every feed, report dead ones
    python source_directory.py --print-lock    # show the cached list

``--verify`` is the check a URL cannot answer on its own: whether fetching it
actually produces a feed. A URL can be perfectly well-formed, https, and public,
and still return a 404 page — which is what happens when a publisher moves its
feed. It exits non-zero if anything is dead, so CI can fail on it.
"""

import concurrent.futures
import ipaddress
import json
import os
import socket
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import requests

# ── Configuration ──────────────────────────────────────────────────────────────

LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sources.lock.json')

SOURCES_API_KEY = os.environ.get('SOURCES_API_KEY')

# Refuse a list that is empty or absurdly large, and refuse one that has moved
# too far from the lock in a single step — both are the shapes a compromised or
# half-written directory would take.
MIN_SOURCES = 5
MAX_SOURCES = 500
MAX_CHURN   = 0.30      # fraction of the locked list that may be lost at once

FETCH_TIMEOUT = 30
MAX_REDIRECTS = 3


class SourceDirectoryError(Exception):
    """Raised when the remote list cannot be used and the lock should win."""


# ── URL safety ─────────────────────────────────────────────────────────────────
# Mirrors api_validate_source_url() in the frontend's api/_bootstrap.php. It is
# repeated here on purpose: that check runs when an admin saves a URL, this one
# runs immediately before the request is made, and only this one sees redirects.

_BLOCKED_PORTS = None   # only 443 is allowed, so nothing to enumerate


def _ip_is_public(ip_str):
    """True only for a globally routable address."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    # is_global excludes loopback, link-local (169.254.169.254 included),
    # private ranges, and the reserved/benchmark blocks. Carrier-grade NAT is
    # covered by is_private in Python 3.9+, checked explicitly for older builds.
    if not ip.is_global:
        return False
    if ip.version == 4 and ip in ipaddress.ip_network('100.64.0.0/10'):
        return False
    return True


def feed_url_error(url):
    """Return '' if this URL is safe to fetch, else a human-readable reason."""
    if not url or not isinstance(url, str):
        return 'missing URL'
    url = url.strip()
    if len(url) > 2000:
        return 'URL is too long'

    parts = urlsplit(url)
    if parts.scheme != 'https':
        return 'not https'
    if parts.username or parts.password:
        return 'URL carries credentials'
    try:
        port = parts.port
    except ValueError:
        return 'invalid port'
    if port not in (None, 443):
        return f'non-standard port {port}'

    host = (parts.hostname or '').strip('.').lower()
    if not host:
        return 'no hostname'

    # A bare IP literal is never a real feed and would skip the DNS check below.
    try:
        ipaddress.ip_address(host)
        return 'URL is a bare IP address'
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return f'hostname does not resolve ({e.strerror or e})'

    # Every address it resolves to must be public: one public and one private
    # answer is still a way in.
    for info in infos:
        ip = info[4][0]
        if not _ip_is_public(ip):
            return f'resolves to non-public address {ip}'
    return ''


def fetch_feed(url, timeout=15, user_agent='Mozilla/5.0 (compatible; RallyNewsBot/1.0)'):
    """Fetch a feed, re-validating the URL at every redirect hop.

    ``requests`` follows redirects itself, which would let a URL that passes the
    check above land somewhere that does not. Redirects are handled manually so
    each hop is validated before it is followed.

    Returns the response, or raises SourceDirectoryError naming the hop that
    failed. Callers that only want the bytes should catch that.
    """
    seen = []
    for _ in range(MAX_REDIRECTS + 1):
        err = feed_url_error(url)
        if err:
            where = f' (redirected from {seen[-1]})' if seen else ''
            raise SourceDirectoryError(f'unsafe feed URL {url}{where}: {err}')

        resp = requests.get(
            url, timeout=timeout, allow_redirects=False,
            headers={'User-Agent': user_agent},
        )
        if resp.is_redirect or resp.is_permanent_redirect:
            target = resp.headers.get('Location')
            if not target:
                raise SourceDirectoryError(f'{url}: redirect with no Location')
            seen.append(url)
            url = requests.compat.urljoin(url, target)
            continue
        return resp

    raise SourceDirectoryError(f'too many redirects (>{MAX_REDIRECTS}) starting at {seen[0]}')


# ── Country → continent ────────────────────────────────────────────────────────
# The directory records each source's country; the scraper's per-run coverage
# check works in continents. This is the bridge. It agrees with the
# SOURCE_CONTINENTS map in scraper.py for 40 of its 41 sources — the exception
# is Reuters, which the directory places in Canada (Thomson Reuters' head
# office) and the old map placed in Europe (the newsroom that files the copy).
# The dashboard's country decides it now.

_CONTINENTS = {
    'North America': 'AG AI AW BB BL BM BQ BS BZ CA CR CU CW DM DO GD GL GP GT HN HT JM '
                     'KN KY LC MF MQ MS MX NI PA PM PR SV SX TC TT US VC VG VI',
    'South America': 'AR BO BR CL CO EC FK GF GY PE PY SR UY VE',
    'Europe':        'AD AL AT AX BA BE BG BY CH CY CZ DE DK EE ES FI FO FR GB GG GI GR '
                     'HR HU IE IM IS IT JE LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS '
                     'RU SE SI SJ SK SM UA VA',
    'Africa':        'AO BF BI BJ BW CD CF CG CI CM CV DJ DZ EG EH ER ET GA GH GM GN GQ '
                     'GW KE KM LR LS LY MA MG ML MR MU MW MZ NA NE NG RE RW SC SD SH SL '
                     'SN SO SS ST SZ TD TG TN TZ UG YT ZA ZM ZW',
    'Asia':          'AE AF AM AZ BD BH BN BT CC CN CX GE HK ID IL IN IO IQ IR JO JP KG '
                     'KH KP KR KW KZ LA LB LK MM MN MO MV MY NP OM PH PK PS QA SA SG SY '
                     'TH TJ TL TM TR TW UZ VN YE',
    'Oceania':       'AS AU CK FJ FM GU KI MH MP NC NF NR NU NZ PF PG PN PW SB TK TO TV '
                     'UM VU WF WS',
}

COUNTRY_CONTINENT = {
    code: continent
    for continent, codes in _CONTINENTS.items()
    for code in codes.split()
}


def continent_for_country(code):
    """Continent name for an ISO 3166-1 alpha-2 code, or None if unknown."""
    if not code:
        return None
    return COUNTRY_CONTINENT.get(code.strip().upper())


# ── Lockfile ───────────────────────────────────────────────────────────────────

def load_lock(path=None):
    """The last known good list, or None when there isn't one yet."""
    path = path or LOCK_PATH
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"  Warning: {os.path.basename(path)} unreadable ({e}) — ignoring it")
        return None
    sources = data.get('sources') if isinstance(data, dict) else None
    return sources if isinstance(sources, list) and sources else None


def write_lock(sources, path=None):
    """Persist the list. Sorted and indented so the git diff is readable."""
    path = path or LOCK_PATH
    payload = {
        'note': 'Written by source_directory.py from the Rally Admin source '
                'directory. Do not edit by hand — edit the Sources tab.',
        'count': len(sources),
        'sources': sorted(sources, key=lambda s: s['source'].lower()),
    }
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write('\n')
    os.replace(tmp, path)


# ── Fetch + validate ───────────────────────────────────────────────────────────

def sources_api_url():
    """Sibling of NEWS_API_URL, the same way balance.php and the others are."""
    base = os.environ.get('NEWS_API_URL')
    if not base:
        return None
    return base.rsplit('/', 1)[0] + '/sources.php'


def fetch_remote(url=None, key=None, timeout=FETCH_TIMEOUT):
    """GET the directory. Raises SourceDirectoryError on any failure."""
    url = url or sources_api_url()
    key = key or SOURCES_API_KEY
    if not url:
        raise SourceDirectoryError('NEWS_API_URL is not set')
    if not key:
        raise SourceDirectoryError('SOURCES_API_KEY is not set')

    try:
        resp = requests.get(url, headers={'X-Sources-Key': key}, timeout=timeout)
    except requests.RequestException as e:
        raise SourceDirectoryError(f'request failed: {type(e).__name__}: {e}')

    if resp.status_code != 200:
        raise SourceDirectoryError(f'HTTP {resp.status_code}: {resp.text[:200]}')
    try:
        data = resp.json()
    except ValueError:
        raise SourceDirectoryError('response was not JSON')
    if not isinstance(data, dict) or not isinstance(data.get('sources'), list):
        raise SourceDirectoryError('response had no sources list')
    return data


def validate_sources(raw):
    """Split the API's rows into usable sources and rejected ones.

    A row is kept only with a name and a feed URL that passes feed_url_error().
    Rejections are returned rather than raised, so one bad row cannot take the
    whole run down — but they are always reported.
    """
    kept, rejected = [], []
    seen = set()
    for row in raw:
        if not isinstance(row, dict):
            rejected.append(('(malformed row)', 'not an object'))
            continue
        name = (row.get('source') or '').strip()
        if not name:
            rejected.append(('(unnamed)', 'no source name'))
            continue
        if name in seen:
            rejected.append((name, 'duplicate name'))
            continue

        feed = (row.get('feed_url') or '').strip()
        err = feed_url_error(feed)
        if err:
            rejected.append((name, err))
            continue

        seen.add(name)
        country = (row.get('country') or '').strip().upper()
        kept.append({
            'source':    name,
            'feed_url':  feed,
            'website':   (row.get('website') or '').strip(),
            'country':   country,
            'continent': continent_for_country(country),
            'paywalled': 1 if row.get('paywalled') else 0,
            'metered':   1 if row.get('metered') else 0,
        })
    return kept, rejected


def is_plausible(sources, lock):
    """Reject a list that is empty, oversized, or a big jump from the lock.

    Returns '' when the list looks usable, else the reason it doesn't.
    """
    if len(sources) < MIN_SOURCES:
        return f'only {len(sources)} usable sources (minimum {MIN_SOURCES})'
    if len(sources) > MAX_SOURCES:
        return f'{len(sources)} sources exceeds the {MAX_SOURCES} ceiling'
    if not lock:
        return ''

    old = {s['source']: s.get('feed_url') for s in lock}
    new = {s['source']: s.get('feed_url') for s in sources}

    # Only losses count. Adding sources is what a newsroom does — each new URL
    # is individually validated before it is ever fetched, and an addition
    # cannot take away coverage — so a batch of additions must not get the whole
    # list refused and the admin's work silently ignored. Mass REMOVAL or mass
    # REPOINTING is the shape a replaced or hijacked list takes, and that is
    # what this still refuses.
    lost = {n for n in old if n not in new}
    moved = {n for n in set(old) & set(new) if old[n] != new[n]}
    churn = len(lost | moved) / max(len(old), 1)
    if churn > MAX_CHURN:
        return (f'{len(lost)} of {len(old)} sources removed and {len(moved)} '
                f'repointed ({churn:.0%} > {MAX_CHURN:.0%} limit)')
    return ''


def load_sources(verbose=True):
    """The list to scrape, and where it came from.

    Never raises and never returns nothing usable: on any failure it falls back
    to the lockfile, and the caller decides what to do if that is empty too.

    Returns (sources, origin) where origin is 'remote', 'lock' or 'none'.
    """
    lock = load_lock()

    try:
        data = fetch_remote()
    except SourceDirectoryError as e:
        if verbose:
            print(f"  Source directory unavailable ({e})")
            print(f"  Falling back to sources.lock.json"
                  f"{f' ({len(lock)} sources)' if lock else ' — which is empty'}")
        return (lock or [], 'lock' if lock else 'none')

    kept, rejected = validate_sources(data['sources'])
    if verbose:
        print(f"  Source directory: {len(kept)} usable of {len(data['sources'])} served")
        for name in data.get('without_feed') or []:
            print(f"    - {name}: listed with no feed, cannot be scraped")
        for name, why in rejected:
            print(f"    ! {name}: rejected — {why}")

    reason = is_plausible(kept, lock)
    if reason:
        if verbose:
            print(f"  Refusing the served list: {reason}")
            print(f"  Falling back to sources.lock.json"
                  f"{f' ({len(lock)} sources)' if lock else ' — which is empty'}")
        return (lock or [], 'lock' if lock else 'none')

    if verbose:
        report_changes_since(lock, kept)

    try:
        write_lock(kept)
    except OSError as e:
        if verbose:
            print(f"  Warning: could not update the lockfile ({e})")
    return (kept, 'remote')


def report_changes_since(lock, sources):
    """Say what changed since the last run, if anything.

    Compared against the lockfile rather than the maps in scraper.py: those are
    a frozen fallback that the dashboard is expected to drift away from, so
    diffing them would grow into noise. The lock is what the previous run
    actually used, which makes every line here something that just changed —
    and the difference between "we removed that on purpose" and "we deleted it
    by accident".
    """
    if not lock:
        return
    old = {s['source']: s.get('feed_url') for s in lock}
    new = {s['source']: s.get('feed_url') for s in sources}

    added    = sorted(set(new) - set(old))
    removed  = sorted(set(old) - set(new))
    repointed = sorted(n for n in set(old) & set(new) if old[n] != new[n])
    if not (added or removed or repointed):
        return

    if added:     print(f"  Added since the last run: {', '.join(added)}")
    if removed:   print(f"  No longer listed, so no longer scraped: {', '.join(removed)}")
    for name in repointed:
        print(f"  Feed changed for {name}: {old[name]} -> {new[name]}")


# ── Feed verification ──────────────────────────────────────────────────────────
# feed_url_error() answers "is this URL safe to fetch". This answers the other
# half — "does fetching it actually produce a feed" — which is the check that
# catches a URL that is perfectly well-formed and simply wrong.
#
# Content-Type is deliberately not consulted. Plenty of publishers serve a feed
# as application/octet-stream, which makes a browser download it rather than
# display it; that is a browser presentation decision and says nothing about
# whether the bytes are a feed. Only the parsed root element decides.

FEED_ROOTS = ('rss', 'feed', 'rdf')


def verify_feed(url, timeout=20):
    """Fetch a feed URL and confirm the response really is a feed.

    Returns (ok, detail). Never raises: a failure is a result, not an error.
    """
    try:
        resp = fetch_feed(url, timeout=timeout)
    except SourceDirectoryError as e:
        return False, str(e)
    except requests.RequestException as e:
        return False, f'{type(e).__name__}: {e}'

    if resp.status_code != 200:
        return False, f'HTTP {resp.status_code}'
    if not resp.content:
        return False, 'empty response'

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        head = resp.content[:80].decode('utf-8', 'replace').strip().replace('\n', ' ')
        return False, f'not XML ({e}) — starts: {head!r}'

    tag = root.tag.split('}', 1)[-1].lower()
    if tag not in FEED_ROOTS:
        return False, f'XML root is <{tag}>, not a feed'

    items = sum(1 for _ in root.iter() if _.tag.split('}', 1)[-1].lower() in ('item', 'entry'))
    return True, f'{tag}, {items} entries, {len(resp.content)} bytes'


def verify_all(sources, workers=8, timeout=20):
    """Verify every source's feed concurrently. Returns [(source, ok, detail)]."""
    def check(s):
        ok, detail = verify_feed(s['feed_url'], timeout=timeout)
        return (s['source'], ok, detail)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(check, sources))
    return sorted(results, key=lambda r: (r[1], r[0].lower()))


def report_verification(results):
    """Print verification results. Returns the number that failed."""
    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        if not ok:
            print(f"  DEAD  {name:<28} {detail}")
    for name, ok, detail in results:
        if ok:
            print(f"  ok    {name:<28} {detail}")
    print(f"\n{len(results) - len(failed)}/{len(results)} feeds returned a parseable feed")
    if failed:
        print("\nFeeds needing attention in the Sources tab:")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")
    return len(failed)


# ── Comparison against the built-in maps ───────────────────────────────────────

def diff_against_code(sources, rss_feeds, whitelist, continents):
    """Compare the served list with the maps still hardcoded in scraper.py."""
    live_code = {n: rss_feeds[n] for n in rss_feeds if n in whitelist}
    remote = {s['source']: s for s in sources}

    return {
        'only_in_directory': sorted(set(remote) - set(live_code)),
        'only_in_code':      sorted(set(live_code) - set(remote)),
        'feed_differs':      sorted(
            (n, live_code[n], remote[n]['feed_url'])
            for n in set(remote) & set(live_code)
            if live_code[n] != remote[n]['feed_url']
        ),
        'continent_differs': sorted(
            (n, continents.get(n), remote[n]['continent'])
            for n in set(remote) & set(live_code)
            if n in continents and continents[n] != remote[n]['continent']
        ),
    }


def report_diff(diff):
    """Print the comparison against the code. Returns True if anything differed."""
    any_diff = any(diff.values())
    if not any_diff:
        print("  Source directory matches the hardcoded list exactly")
        return False

    for name in diff['only_in_directory']:
        print(f"    + {name}: in the dashboard, not in scraper.py")
    for name in diff['only_in_code']:
        print(f"    - {name}: in scraper.py, not in the dashboard")
    for name, code_url, remote_url in diff['feed_differs']:
        print(f"    ~ {name}: feed differs")
        print(f"        code:      {code_url}")
        print(f"        dashboard: {remote_url}")
    for name, code_c, remote_c in diff['continent_differs']:
        print(f"    ~ {name}: continent differs — code {code_c}, dashboard {remote_c}")
    return True


# ── CLI ────────────────────────────────────────────────────────────────────────

def _main(argv):
    if '--print-lock' in argv:
        lock = load_lock()
        if not lock:
            print('No sources.lock.json yet.')
            return 1
        for s in lock:
            print(f"{s['source']:<28} {s.get('country', '??'):<4} "
                  f"{s.get('continent') or '?':<15} {s['feed_url']}")
        print(f"\n{len(lock)} sources")
        return 0

    if '--verify' in argv:
        # Fetches every feed, so it is opt-in rather than part of --check.
        sources, origin = load_sources()
        if not sources:
            print('No sources to verify.')
            return 1
        print(f"Verifying {len(sources)} feeds from {origin}…\n")
        # Exit non-zero when anything is dead, so CI can fail on it.
        return 1 if report_verification(verify_all(sources)) else 0

    if '--check' in argv:
        sources, origin = load_sources()
        print(f"Loaded {len(sources)} sources from {origin}")
        if origin == 'none':
            return 1
        try:
            import scraper
        except Exception as e:            # pragma: no cover - import side effects
            print(f"(could not import scraper.py to diff: {e})")
            return 0
        print("\nAgainst the hardcoded maps in scraper.py:")
        report_diff(diff_against_code(
            sources, scraper.RSS_FEEDS, scraper.WHITELISTED_SOURCES, scraper.SOURCE_CONTINENTS
        ))
        return 0

    print(__doc__.strip().split('\n\n')[0])
    print('\nUsage: python source_directory.py [--check | --verify | --print-lock]')
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
