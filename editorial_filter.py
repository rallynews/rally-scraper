#!/usr/bin/env python3
"""What Rally counts as good news, read from Rally Studio.

The judgement applied to every candidate story used to be a prompt hardcoded in
``scraper.py``. It now lives in a table Studio edits, so the newsroom can tune
what Rally publishes without a commit and a deploy.

HOW A STORY IS SCORED

The model is given a job (``prompt``), a person to be while it does it
(``personality``), and a list of statements about the story (``questions``). It
rates every statement from 1 (disagree completely) to 10 (agree completely), in
that person's voice, and the AVERAGE of those answers is the story's score. A
story QUALIFIES when both hold:

* the average is at least ``min_score``, and
* fewer than ``veto_count`` statements came back as a 1.

Qualifying is not the same as being published. A run is filled in two tiers,
best first, by :func:`select_for_run`:

1. Every qualifying story scoring ``strong_score`` or above goes in, with no
   cap. Find twenty at 7.5+ and all twenty are published, and nothing from
   below is used at all.
2. Only if that leaves the run short of ``target`` does it top up, highest score
   first, from the stories between ``min_score`` and ``strong_score`` — and it
   stops the moment the target is met. That band is a shortfall filler, never a
   bulk source.

A run that ends up short still publishes what it has. A run that qualifies
nothing publishes nothing and is reported as a blank scrape, which Rally Studio
shows on the Filters page.

``HARD_FLOOR`` is under all of it: a story averaging below it is never
published, whatever a config or a stale lockfile says. The configurable cutoff
band sits above the floor, so in normal use it never comes up — it is there so
that a corrupted config cannot quietly put a 4 on the site.

The score is INTERNAL. It is stored against the article for the dashboard and is
absent from every public read in the API. Readers never see a number attached to
a story, and nothing here should ever put one in front of them.

VERSIONS

Studio keeps every set of rules it has ever had and deploys one of them.
``api/filter.php`` serves the deployed one, so a scheduled scrape always runs
against rules someone deliberately deployed — saving a draft in Studio changes
nothing here. ``FILTER_VERSION_ID`` in the environment points a run at one
specific version instead, for trying a draft out before deploying it; a run like
that never writes the lockfile, so a dry run cannot leave a draft behind as the
fallback everything else falls back to.

What Studio owns is the prose, the questions, the examples and the thresholds.
Where the story goes in the prompt and the format of the answer stay in this
file: an editor rewriting a personality should not be able to break the
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

# The cutoff is narrower than the scale it sits on. Below 6 the filter stops
# filtering — 5 is "moderate" — and above 9 so little qualifies that a run
# exhausts every feed and still falls short of MIN_NEW_ARTICLES. Enforced here
# as well as in Studio, so a stale or tampered value cannot widen it.
CUTOFF_MIN = 6.0
CUTOFF_MAX = 9.0

# The line under everything. Not a setting, not part of the band above, and not
# reachable by any cutoff this file will accept: a story averaging below 5 is
# never published, whatever the API, a lockfile or a later change to the band
# says. Mirrors FILTER_HARD_FLOOR in the frontend's api/_bootstrap.php.
HARD_FLOOR = 5.0

QUESTIONS_MIN = 3
QUESTIONS_MAX = 20

# Mirrors FILTER_DEFAULT_* in the frontend's api/_bootstrap.php. Kept in step so
# the scraper behaves the same when the API is unreachable and there is no lock.
DEFAULT_CUTOFF = 6.5   # tier 2: the shortfall filler
DEFAULT_STRONG = 7.5   # tier 1: what a run is filled with first
DEFAULT_VETO = 2

DEFAULT_PROMPT = (
    "You are an expert news curator. You search a selection of trusted "
    "international news sources' RSS feeds and select the positive stories that "
    "promise hope, promise, reconciliation, peace, development, compromise, "
    "success, and/or other distinctly and clearly positive themes.\n\n"
    "You flag those articles using a score built from the scoring questions "
    "below. The questions decide how you should feel and react to a story. To "
    "pick out the stories that represent genuinely positive developments you "
    "inhabit an artificial personality: act as though you are a person with that "
    "personality and a deep understanding of the news industry, and answer the "
    "questions as they would.\n\n"
    "Whatever the personality feels, a positive story must always clear these "
    "objective rules:\n"
    "- Positive news includes progress, achievements, solutions, help, "
    "innovation, recovery and cooperation.\n"
    "- Positive news is not controversial, and actively shows progress.\n"
    "- Positive news is not about the acquisition of wealth by large "
    "corporations, or about corporations making deals with each other that do "
    "not benefit humanity."
)

DEFAULT_PERSONALITY = (
    "You are a woman from the United States who is 56 years old. You are white. "
    "You have a combined household income of $386,000, all of which comes from "
    "your husband, since you are a stay-at-home mom. You are a member of the "
    "Democratic Party and vote party line every time. However, you are disgusted "
    "with the current state of the political party. You have stopped your "
    "monthly donation to the party and instead choose to donate to climate "
    "charities and charities in Africa. You hold a college degree from Vassar, "
    "where you studied Communications. You have been a stay-at-home mom of three "
    "kids since quitting your career after your eldest daughter was born. Your "
    "youngest son was adopted from Ethiopia, and you are a member of the city's "
    "school board. You owned a local crafts shop selling local arts and crafts "
    "for several years called \"Make it!\". You were born and raised in "
    "Pennsylvania but you moved to a Connecticut suburb when your husband, who "
    "works in finance, got a promotion at his investment bank.\n\n"
    "You have 3 kids. Your eldest daughter followed you to Vassar, where she "
    "studied journalism and is now working as a Social Media Coordinator and "
    "aspiring climate change influencer. Your middle son works in finance, and "
    "is engaged. Your youngest is in college.\n\n"
    "You are very plugged into the news, and your primary sources are Instagram "
    "and Facebook. You follow center-left-leaning sources, and are somewhat "
    "worried about socialism. News scares you, and you want a place that makes "
    "you feel happy. You're interested in wellness, meditation, life hacks, and "
    "medical advancements. You're somewhat worried about the potential for job "
    "loss and misalignment caused by AI, but you know that AI is the future and "
    "want to see it implemented in an ethical manner. You are very worried about "
    "climate change and you choose climate-friendly options whenever possible."
)

DEFAULT_QUESTIONS = [
    'This story makes me feel happy.',
    'This story is primarily positive.',
    'The news talked about in this story will provide a net positive to society.',
    'I will benefit personally from the contents of this article.',
    'This article makes me feel a strong emotion.',
    'The content discussed in this story will make the world better for others.',
    'This story will make others very happy, and that makes me happy.',
    'This story is not controversial.',
    'There are few or no negative effects of the developments outlined in this story.',
    'This story will make the world better in the future.',
]

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
    'version_id': None,
    'version_name': 'built-in defaults',
    'prompt': DEFAULT_PROMPT,
    'personality': DEFAULT_PERSONALITY,
    'questions': list(DEFAULT_QUESTIONS),
    'min_score': DEFAULT_CUTOFF,
    'strong_score': DEFAULT_STRONG,
    'veto_count': DEFAULT_VETO,
    'examples': [{'headline': h, 'score': s} for h, s in DEFAULT_EXAMPLES],
}


# ── Fetch ──────────────────────────────────────────────────────────────────────

def filter_api_url():
    """Sibling of NEWS_API_URL, like sources.php and the others."""
    base = os.environ.get('NEWS_API_URL')
    return base.rsplit('/', 1)[0] + '/filter.php' if base else None


def requested_version():
    """The version this run was pointed at, or None for whichever is deployed.

    Set by the `filter_version` input on the scrape workflow. Anything that is
    not a positive integer is ignored rather than guessed at — an unattended
    dispatch sends no inputs at all, which arrives here as an empty string.
    """
    raw = (os.environ.get('FILTER_VERSION_ID') or '').strip()
    if not raw.isdigit():
        return None
    return int(raw) or None


def _clean_score(value):
    """A usable whole 1-10 answer, or None."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return n if SCORE_MIN <= n <= SCORE_MAX else None


def _clean_average(value):
    """A usable 1-10 average, to one decimal, or None."""
    try:
        n = round(float(value), 1)
    except (TypeError, ValueError):
        return None
    return n if SCORE_MIN <= n <= SCORE_MAX else None


def validate_config(data):
    """Coerce an API response into a usable config, or raise.

    An empty prompt, an empty personality or an empty question list would leave
    the model judging stories against nothing, which is worse than judging them
    against the defaults — so an incomplete response is refused rather than
    half-applied.
    """
    if not isinstance(data, dict):
        raise source_directory.SourceDirectoryError('response was not an object')

    prompt = (data.get('prompt') or '').strip()
    personality = (data.get('personality') or '').strip()
    if not prompt:
        raise source_directory.SourceDirectoryError('no prompt in the response')
    if not personality:
        raise source_directory.SourceDirectoryError('no personality in the response')

    questions = []
    for q in data.get('questions') or []:
        q = (q or '').strip() if isinstance(q, str) else ''
        if q:
            questions.append(q)
    if len(questions) < QUESTIONS_MIN:
        raise source_directory.SourceDirectoryError(
            f'only {len(questions)} scoring questions, need at least {QUESTIONS_MIN}')
    if len(questions) > QUESTIONS_MAX:
        raise source_directory.SourceDirectoryError(
            f'{len(questions)} scoring questions is more than the {QUESTIONS_MAX} allowed')

    cutoff = _clean_average(data.get('min_score'))
    if cutoff is None or not (CUTOFF_MIN <= cutoff <= CUTOFF_MAX) or cutoff < HARD_FLOOR:
        # Refused rather than clamped: a cutoff outside the band means the config
        # is not one this scraper should be judging stories by, and quietly
        # moving it would change what gets published without saying so. The floor
        # is checked as well as the band, not instead of it — today that is
        # redundant, and that is the point.
        raise source_directory.SourceDirectoryError(
            f'cutoff {data.get("min_score")!r} is outside the allowed '
            f'{CUTOFF_MIN}-{CUTOFF_MAX} range')

    # The preferred score is the tier a run fills from first. A missing or silly
    # value falls back to the default rather than failing the whole config: a run
    # with the tiers slightly wrong still publishes good news, and refusing here
    # would mean judging stories by the built-in defaults instead.
    strong = _clean_average(data.get('strong_score'))
    if strong is None or strong <= cutoff or strong < HARD_FLOOR:
        strong = max(cutoff, DEFAULT_STRONG)

    veto = _clean_score(data.get('veto_count')) or DEFAULT_VETO

    examples = []
    for row in data.get('examples') or []:
        if not isinstance(row, dict):
            continue
        headline = (row.get('headline') or '').strip()
        score = _clean_score(row.get('score'))
        if headline and score is not None:
            examples.append({'headline': headline, 'score': score})

    return {
        'version_id': data.get('version_id'),
        'version_name': (data.get('version_name') or '').strip() or 'unnamed',
        'is_deployed': bool(data.get('is_deployed', True)),
        'prompt': prompt,
        'personality': personality,
        'questions': questions,
        'min_score': cutoff,
        'strong_score': strong,
        'veto_count': veto,
        'examples': examples,
    }


def fetch_remote(url=None, key=None, version_id=None, timeout=source_directory.FETCH_TIMEOUT):
    """GET the filter. Raises SourceDirectoryError on any failure."""
    url = url or filter_api_url()
    key = key or source_directory.SOURCES_API_KEY
    if not url:
        raise source_directory.SourceDirectoryError('NEWS_API_URL is not set')
    if not key:
        raise source_directory.SourceDirectoryError('SOURCES_API_KEY is not set')
    if version_id:
        url = f'{url}?version={int(version_id)}'
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
    payload['note'] = ('Written by editorial_filter.py from the filter version deployed in '
                       'Rally Studio. Do not edit by hand — edit and deploy in News > Filters.')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write('\n')
    os.replace(tmp, path)


def load_filter(verbose=True, version_id=None):
    """The filter to judge by, and where it came from ('remote', 'lock', 'defaults')."""
    version_id = version_id if version_id is not None else requested_version()
    try:
        config = validate_config(fetch_remote(version_id=version_id))
    except source_directory.SourceDirectoryError as e:
        lock = load_lock()
        if verbose:
            print(f"  Editorial filter unavailable ({e})")
            print(f"  Falling back to {'filter.lock.json' if lock else 'the built-in defaults'}")
        return (lock or dict(DEFAULTS)), ('lock' if lock else 'defaults')

    # The lockfile is the fallback every future run uses when the API is
    # unreachable, so only the deployed version may write it. A run pointed at a
    # draft is a dry run: it judges stories by the draft and leaves no trace.
    if config.get('is_deployed', True):
        try:
            write_lock(config)
        except OSError as e:
            if verbose:
                print(f"  Warning: could not update the filter lockfile ({e})")
    elif verbose:
        print(f"  Running against undeployed version {config.get('version_id')} "
              f"— the lockfile is left alone")
    return config, 'remote'


# ── Prompt ─────────────────────────────────────────────────────────────────────

def build_prompt(config, title, summary):
    """Assemble the scoring prompt.

    Studio owns the prose; this owns the structure — where the story goes and how
    the answer must be formatted. Keeping the answer contract out of the editable
    text means a prose edit can never stop the output from parsing.
    """
    questions = config['questions']
    parts = [
        config['prompt'].strip(),
        '',
        'The personality you inhabit while you answer:',
        config['personality'].strip(),
        '',
    ]

    examples = sorted(config.get('examples') or [], key=lambda e: -e['score'])
    if examples:
        parts.append(
            f"Stories already scored, for calibration ({SCORE_MAX} = the best news "
            f"imaginable, 5 = moderate, {SCORE_MIN} = the worst):"
        )
        for e in examples:
            parts.append(f"{e['score']} - {e['headline']}")
        parts.append('')

    parts += [
        'The story to score:',
        f"Title: {title}",
        f"Summary: {summary}",
        '',
        f"Rate each of the following {len(questions)} statements about that story "
        f"from {SCORE_MIN} (disagree completely) to {SCORE_MAX} (agree "
        f"completely), as the person described above would:",
    ]
    for i, q in enumerate(questions, 1):
        parts.append(f"{i}. {q}")

    parts += [
        '',
        f"Answer with ONLY {len(questions)} whole numbers from {SCORE_MIN} to "
        f"{SCORE_MAX}, one per statement, in order, separated by commas. "
        f"No words, no explanation.",
    ]
    return '\n'.join(parts)


def parse_scores(text, expected):
    """The per-question answers in a model reply, or None if there isn't a set.

    Models pad answers, so the numbers are dug out rather than parsed strictly: a
    JSON array if there is one, otherwise every integer in the reply. Two shapes
    are accepted — the bare list that was asked for, and the numbered list models
    fall into anyway ("1. 8", "2. 7"), where the question numbers have to be
    dropped before the answers make sense.

    Anything else is treated as no answer rather than salvaged. A reply with nine
    numbers when ten were asked for is a reply that skipped a question, and
    averaging it would be averaging something the model never said.
    """
    if not text or expected <= 0:
        return None
    text = str(text)

    numbers = None
    array = re.search(r'\[[^\]\[]*\]', text)
    if array:
        try:
            parsed = json.loads(array.group())
            if isinstance(parsed, list):
                numbers = parsed
        except ValueError:
            numbers = None

    if numbers is None:
        numbers = re.findall(r'\d+(?:\.\d+)?', text)
        # A numbered list doubles the count, with the question numbers running
        # 1, 2, 3… in the odd positions. Strip those and keep the answers.
        if len(numbers) == expected * 2:
            labels = [_clean_score(n) for n in numbers[0::2]]
            if labels == list(range(1, expected + 1)):
                numbers = numbers[1::2]

    if len(numbers) != expected:
        return None

    scores = [_clean_score(n) for n in numbers]
    return None if any(s is None for s in scores) else scores


# ── Verdict ────────────────────────────────────────────────────────────────────

def average_of(scores):
    """The story's score: the mean of its answers, to one decimal."""
    return round(sum(scores) / len(scores), 1)


def evaluate(config, scores):
    """(average, qualified, why_not) for one story's answers.

    Three ways to fail. The floor comes first because nothing overrides it, then
    the veto, because it is the more specific of the remaining two: a story a
    reader completely disagrees with on two counts is not good news however well
    it did on the other eight.

    Qualifying is not the same as being published — see :func:`select_for_run`.
    """
    average = average_of(scores)
    if average < HARD_FLOOR:
        return average, False, f"below the {HARD_FLOOR} floor"
    ones = sum(1 for s in scores if s == SCORE_MIN)
    if ones >= config['veto_count']:
        return average, False, f"{ones} answers of {SCORE_MIN}"
    if average < config['min_score']:
        return average, False, f"below the {config['min_score']} cutoff"
    return average, True, ''


def is_preferred(config, average):
    """Tier 1: a story good enough to go in whatever else the run finds."""
    return average >= config['strong_score']


def select_for_run(config, articles, target, score_key='positivity_score',
                   category_key=None, per_category=None):
    """Choose what a run actually publishes, best first.

    Tier 1 is every qualifying story at ``strong_score`` or above, uncapped by
    the target: if a run finds twenty of them it publishes twenty and takes
    nothing from below. Tier 2 is only reached for when tier 1 leaves the run
    short of ``target``, and then only as far as filling it — the weaker band
    tops a run up, it never bulks one out.

    ``category_key``/``per_category`` apply the newsroom's "no more than N
    stories in any one category" rule to the PUBLISHED set. It has to be applied
    here rather than only as stories are found: a run holds candidates from both
    tiers, and topping up from the fallback band could otherwise push a category
    past its cap. Within a tier the highest scores win the contested slots.

    Returns (published, unused). Unused stories qualified but were not needed —
    the run was full, or their category was.

    Decided at the end of the run rather than as stories are found, because how
    far down the run has to reach depends on how many strong ones it ends up
    with — a story turned away early could be one a thinner run needed.

    A run that ends up short still publishes what it has. Only a run with
    nothing at all publishes nothing, and that is a blank scrape.
    """
    by_score = lambda a: a[score_key]
    preferred = sorted((a for a in articles if is_preferred(config, a[score_key])),
                       key=by_score, reverse=True)
    fallback  = sorted((a for a in articles if not is_preferred(config, a[score_key])),
                       key=by_score, reverse=True)

    counts = {}
    capped = bool(category_key and per_category)

    def has_room(a):
        return not capped or counts.get(a.get(category_key), 0) < per_category

    published, unused = [], []

    def admit(a):
        published.append(a)
        if capped:
            key = a.get(category_key)
            counts[key] = counts.get(key, 0) + 1

    for a in preferred:
        (admit if has_room(a) else unused.append)(a)

    for a in fallback:
        if len(published) >= target or not has_room(a):
            unused.append(a)
        else:
            admit(a)

    return published, unused


# ── CLI ────────────────────────────────────────────────────────────────────────

def _main(argv):
    if '--show' in argv:
        config, origin = load_filter()
        print(f"Filter loaded from: {origin}")
        print(f"Version: {config.get('version_name')} "
              f"(id {config.get('version_id')})")
        print(f"Qualify at {config['min_score']}+ on the average of "
              f"{len(config['questions'])} questions; {config['veto_count']} answers of 1 fail a "
              f"story; nothing under {HARD_FLOOR} ever")
        print(f"A run takes every story at {config['strong_score']}+, and reaches down to "
              f"{config['min_score']} only to fill a shortfall "
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
