#!/usr/bin/env python3
"""What Rally counts as good news, read from the Rally Admin dashboard.

The judgement applied to every candidate story used to be a prompt hardcoded in
``scraper.py``. It now lives in a table the dashboard edits, so the newsroom can
tune what Rally publishes without a commit and a deploy.

Every story is scored **1-10** — 10 is the best news imaginable, 5 is moderate,
1 is the worst — and anything below the cutoff is rejected. That replaces the old
YES/NO answer rather than adding a second question, so a run still makes one AI
call per candidate.

The score is INTERNAL. It is stored against the article for the dashboard and is
absent from every public read in the API. Readers never see a number attached to
a story, and nothing here should ever put one in front of them.

The pieces the dashboard owns:

* ``intro``     - what the model is asked to judge, before the examples
* ``examples``  - worked headlines with their scores, which teach the scale
* ``rules``     - the "score low if..." cases, placed just before the answer
* ``min_score`` - the cutoff a story must reach to be published

Everything else — where the story goes in the prompt, and the answer format —
stays in this file. An admin editing prose should not be able to break the
machine-readable contract by deleting the wrong line.

Falls back to ``filter.lock.json`` and then to the defaults below, so a story is
never judged by an empty prompt.

    python editorial_filter.py --show     # print the prompt that would be used
"""

import json
import os
import re
import sys

import requests

import source_directory

LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'filter.lock.json')

SCORE_MIN = 1
SCORE_MAX = 10

# Mirrors FILTER_DEFAULT_* in the frontend's api/_bootstrap.php. Kept in step so
# the scraper behaves the same when the API is unreachable and there is no lock.
DEFAULT_CUTOFF = 7

DEFAULT_INTRO = (
    "Score this article on how POSITIVE it is as news — progress, achievements, "
    "solutions, help, innovation, recovery, cooperation.\n\n"
    "Positive news is not controversial, and actively shows progress. It is not "
    "about the acquisition of wealth by large corporations, or corporations "
    "making deals with each other that do not benefit humanity."
)

DEFAULT_RULES = (
    "Scoring rules:\n"
    "- 10 is the best news imaginable; 5 is moderate; 1 is the worst.\n"
    "- Score low if it is primarily about big companies or corporate interests "
    "making deals with each other.\n"
    "- Score low if it is neutral, negative, explanatory, or just informational.\n"
    "- Score low if it is about problems, conflicts, crises, or disasters.\n"
    "- Score low if it is an explainer or educational content.\n"
    "- Score low if it is about controversy or debate."
)

DEFAULT_EXAMPLES = [
    ('A Single Infusion Could Suppress H.I.V. for Years, Study Suggests', 9),
    ("Sharp drop in 'forever chemicals' in seabird eggs hailed as win for regulation", 9),
    ('Worksite testing AI to provide early high heat alerts to keep workers safe', 8),
    ('Macron announces €23 billion of investment at Africa summit', 8),
    ('A Writer With a Healthy Appetite, and a Love of New York City', 7),
    ('Innovation abounds in device charging', 7),
    ('How Japan created the ultimate take-away food', 7),
    ("How a Hollywood star's photos inspired The Waterboys' latest album", 7),
    ('A year after his death, we look back at the legacy of David Bowie', 7),
    ('Kennedy Is Driving a Vast Inquiry Into Vaccines, Despite His Public Silence', 3),
    ('Inside the Israeli Voting Controversy That Engulfed Eurovision', 3),
    ('Reflecting Pool Costs Balloon to $13.1 Million, Records Show', 3),
    ('American Passengers Exposed to Hantavirus Begin Quarantine in U.S.', 2),
    ('Emissions rise by 10% over last year, according to new data', 2),
    ('Man Charged With Assassination Attempt at Press Gala Pleads Not Guilty', 1),
]

DEFAULTS = {
    'intro': DEFAULT_INTRO,
    'rules': DEFAULT_RULES,
    'min_score': DEFAULT_CUTOFF,
    'examples': [{'headline': h, 'score': s} for h, s in DEFAULT_EXAMPLES],
}


# ── Fetch ──────────────────────────────────────────────────────────────────────

def filter_api_url():
    """Sibling of NEWS_API_URL, like sources.php and the others."""
    base = os.environ.get('NEWS_API_URL')
    return base.rsplit('/', 1)[0] + '/filter.php' if base else None


def _clean_score(value):
    """A usable 1-10 score, or None."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return n if SCORE_MIN <= n <= SCORE_MAX else None


def validate_config(data):
    """Coerce an API response into a usable config, or raise.

    An empty intro or an empty rules block would leave the model judging stories
    against nothing, which is worse than judging them against the defaults — so
    an incomplete response is refused rather than half-applied.
    """
    if not isinstance(data, dict):
        raise source_directory.SourceDirectoryError('response was not an object')

    intro = (data.get('intro') or '').strip()
    rules = (data.get('rules') or '').strip()
    if not intro:
        raise source_directory.SourceDirectoryError('no instructions in the response')
    if not rules:
        raise source_directory.SourceDirectoryError('no rules in the response')

    cutoff = _clean_score(data.get('min_score'))
    if cutoff is None:
        raise source_directory.SourceDirectoryError(
            f'cutoff {data.get("min_score")!r} is not a score between {SCORE_MIN} and {SCORE_MAX}')

    examples = []
    for row in data.get('examples') or []:
        if not isinstance(row, dict):
            continue
        headline = (row.get('headline') or '').strip()
        score = _clean_score(row.get('score'))
        if headline and score is not None:
            examples.append({'headline': headline, 'score': score})

    return {'intro': intro, 'rules': rules, 'min_score': cutoff, 'examples': examples}


def fetch_remote(url=None, key=None, timeout=source_directory.FETCH_TIMEOUT):
    """GET the filter. Raises SourceDirectoryError on any failure."""
    url = url or filter_api_url()
    key = key or source_directory.SOURCES_API_KEY
    if not url:
        raise source_directory.SourceDirectoryError('NEWS_API_URL is not set')
    if not key:
        raise source_directory.SourceDirectoryError('SOURCES_API_KEY is not set')
    try:
        resp = requests.get(url, headers={'X-Sources-Key': key}, timeout=timeout)
    except requests.RequestException as e:
        raise source_directory.SourceDirectoryError(f'request failed: {type(e).__name__}: {e}')
    if resp.status_code != 200:
        raise source_directory.SourceDirectoryError(f'HTTP {resp.status_code}: {resp.text[:200]}')
    try:
        return resp.json()
    except ValueError:
        raise source_directory.SourceDirectoryError('response was not JSON')


# ── Lockfile ───────────────────────────────────────────────────────────────────

def load_lock(path=None):
    path = path or LOCK_PATH
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"  Warning: {os.path.basename(path)} unreadable ({e}) — ignoring it")
        return None
    try:
        return validate_config(data)
    except source_directory.SourceDirectoryError:
        return None


def write_lock(config, path=None):
    path = path or LOCK_PATH
    payload = dict(config)
    payload['note'] = ('Written by editorial_filter.py from the Rally Admin filter. '
                       'Do not edit by hand — edit the Filter tab.')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write('\n')
    os.replace(tmp, path)


def load_filter(verbose=True):
    """The filter to judge by, and where it came from ('remote', 'lock', 'defaults')."""
    try:
        config = validate_config(fetch_remote())
    except source_directory.SourceDirectoryError as e:
        lock = load_lock()
        if verbose:
            print(f"  Editorial filter unavailable ({e})")
            print(f"  Falling back to {'filter.lock.json' if lock else 'the built-in defaults'}")
        return (lock or dict(DEFAULTS)), ('lock' if lock else 'defaults')

    try:
        write_lock(config)
    except OSError as e:
        if verbose:
            print(f"  Warning: could not update the filter lockfile ({e})")
    return config, 'remote'


# ── Prompt ─────────────────────────────────────────────────────────────────────

def build_prompt(config, title, summary):
    """Assemble the scoring prompt.

    The admin owns the prose; this owns the structure — where the story goes and
    how the answer must be formatted. Keeping the answer contract out of the
    editable text means a prose edit can never stop the output from parsing.
    """
    parts = [config['intro'].strip(), '']

    examples = sorted(config.get('examples') or [], key=lambda e: -e['score'])
    if examples:
        parts.append(
            f"Examples, already scored ({SCORE_MAX} = the best news imaginable, "
            f"5 = moderate, {SCORE_MIN} = the worst):"
        )
        for e in examples:
            parts.append(f"{e['score']} - {e['headline']}")
        parts.append('')

    parts += [
        f"Title: {title}",
        f"Summary: {summary}",
        '',
        config['rules'].strip(),
        '',
        f"Answer with ONLY a whole number from {SCORE_MIN} to {SCORE_MAX}.",
    ]
    return '\n'.join(parts)


def parse_score(text):
    """The score in a model reply, or None if there isn't a usable one.

    Models pad answers ("Score: 8", "8/10", "**8**"), so the first integer in the
    reply is taken. A number outside the scale is treated as no answer rather
    than clamped: a reply of 0 or 47 means the model did not follow the format,
    and guessing what it meant would quietly admit or reject a story.
    """
    if not text:
        return None
    match = re.search(r'-?\d+', str(text))
    if not match:
        return None
    return _clean_score(match.group())


# ── CLI ────────────────────────────────────────────────────────────────────────

def _main(argv):
    if '--show' in argv:
        config, origin = load_filter()
        print(f"Filter loaded from: {origin}")
        print(f"Cutoff: publish at {config['min_score']} or higher "
              f"({len(config.get('examples') or [])} examples)\n")
        print('─' * 70)
        print(build_prompt(config, '<article title>', '<article summary>'))
        print('─' * 70)
        return 0

    print(__doc__.strip().split('\n\n')[0])
    print('\nUsage: python editorial_filter.py --show')
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
