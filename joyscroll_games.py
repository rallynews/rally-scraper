"""Joyscroll's games, made from every story the scraper publishes.

For each story: one trivia question, one Joyword answer (a positive 5-letter
word about the story) and one Crossjoy mini crossword built around words from
the story. All three are tied to the story, so the app can always say "Read
the story".

Runs at the end of every scrape (scraper.py) and by hand:

    python joyscroll_games.py              # make what recent stories are missing
    python joyscroll_games.py --dry-run    # same, but print instead of posting
    python joyscroll_games.py --limit 3    # at most three stories

What it works from is the API's own list of recent stories still missing a
piece (trivia.php?action=pending), so a story that failed one hour is tried
again the next, and a story that fails twice in a week is left alone.

Per story, at most these model calls (MAX_CALLS_PER_RUN caps the run):
  1. writer   — the trivia question, three Joyword candidates, and a few short
                words from the story for the crossword, each with a clue
  2. checker  — a different model family answers the question blind and flags
                anything wrong with it, and sorts every proposed word into
                positive / wholesome / excluded
  3. clues    — upbeat clues for the bank words the grid was filled with
  4. solver   — the checker model sees each clue and its length only, and must
                produce the answer; a clue it can't solve is rewritten once (5,
                6) and then falls back to the word's definition

The article's text is fetched once and kept in memory only. It is never
written to the database, a log or a file: trivia and clues are written fresh,
in Rally's own words, and link back to the source.
"""

import json
import os
import random
import re
import sys
import time
import unicodedata

import requests

import joyscroll_crossword as crossword

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
# The same attribution scraper.py sends: https://openrouter.ai/docs/app-attribution
OPENROUTER_ATTRIBUTION = {
    'HTTP-Referer': 'https://rally.news',
    'X-OpenRouter-Title': 'Rally',
}

NEWS_API_URL = os.environ.get('NEWS_API_URL')
_api_base = NEWS_API_URL.rsplit('/', 1)[0] if NEWS_API_URL else None
WORDS_API_URL = f"{_api_base}/words.php" if _api_base else None
CROSSWORDS_API_URL = f"{_api_base}/crosswords.php" if _api_base else None
TRIVIA_API_URL = f"{_api_base}/trivia.php" if _api_base else None

# Each key writes its own tables and nothing else (see api/_joyscroll.php).
WORDS_API_KEY = os.environ.get('JOYSCROLL_WORDS_API_KEY')
TRIVIA_API_KEY = os.environ.get('JOYSCROLL_TRIVIA_API_KEY')

# Model names are settings, not code: change them in the repo's Actions
# variables. The checker must be a different model family from the writer, so
# the two don't share the same blind spots.
WRITER_MODEL = os.environ.get('TRIVIA_WRITER_MODEL') or 'openai/gpt-4o-mini'
CHECKER_MODEL = os.environ.get('TRIVIA_CHECKER_MODEL') or 'google/gemini-2.0-flash-001'

MAX_CALLS_PER_RUN = int(os.environ.get('JOYSCROLL_MAX_CALLS') or 120)
TIME_BUDGET_S = int(os.environ.get('JOYSCROLL_TIME_BUDGET') or 15 * 60)

USER_AGENT = 'RallyNewsBot/1.0 (+https://rally.news; good-news aggregator)'
# The full article is the best ground for a question. Below this, the page
# probably was not the article (a paywall, a consent wall) and the question is
# written from the publisher's own summary and opening paragraph instead.
FULL_TEXT_MIN = 800
FALLBACK_TEXT_MIN = 200
TEXT_MAX = 12000

TRUE_FALSE_SHARE = 0.4
STORY_WORD_DAYS = 14

THEMES = ['nature', 'food', 'feelings', 'people', 'places', 'home', 'animals', 'music', 'arts',
          'sport', 'science', 'weather', 'time', 'play', 'travel', 'work', 'everyday']

ENABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'words', 'enable-3-5.txt')


def load_dictionary(path=ENABLE_PATH):
    with open(path) as f:
        return {w.strip() for w in f if w.strip()}


# ═══════════════════════════════════════════════════════════════
# MODEL CALLS
# ═══════════════════════════════════════════════════════════════

class Budget:
    """Counts model calls and the clock, so a run can never run away."""

    def __init__(self, max_calls=MAX_CALLS_PER_RUN, seconds=TIME_BUDGET_S):
        self.max_calls = max_calls
        self.calls = 0
        self.deadline = time.time() + seconds

    def can_call(self, n=1):
        return self.calls + n <= self.max_calls and time.time() < self.deadline

    def spend(self):
        self.calls += 1


def parse_json_reply(text):
    """The JSON object in a model's reply, tolerating code fences and chatter
    around it. None when there isn't one."""
    if not text:
        return None
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def call_json(model, prompt, budget, max_tokens=900, timeout=45):
    """One model call that must answer with a JSON object."""
    if not OPENROUTER_API_KEY or not budget.can_call():
        return None
    budget.spend()
    try:
        resp = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {OPENROUTER_API_KEY}',
                'Content-Type': 'application/json',
                **OPENROUTER_ATTRIBUTION,
            },
            json={
                'model': model,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': max_tokens,
                'temperature': 0.4,
                'response_format': {'type': 'json_object'},
            },
            timeout=timeout,
        )
        if resp.status_code != 200:
            print(f"      ✗ {model}: {resp.status_code} {resp.text[:160]}")
            return None
        return parse_json_reply(resp.json()['choices'][0]['message']['content'])
    except Exception as e:
        print(f"      ✗ {model}: {type(e).__name__}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# THE ARTICLE TEXT (in memory only)
# ═══════════════════════════════════════════════════════════════

def fetch_article_text(url):
    """The article's main text, fetched once, or '' when it can't be had.
    Never stored anywhere — the caller keeps it in memory for one story."""
    try:
        resp = requests.get(url, timeout=15, headers={'User-Agent': USER_AGENT})
        if not resp.ok or 'html' not in resp.headers.get('Content-Type', 'text/html'):
            return ''
        page = resp.text
    except Exception:
        return ''
    try:
        import trafilatura  # optional: better main-text extraction
        text = trafilatura.extract(page, include_comments=False, include_tables=False) or ''
    except Exception:
        text = ''
    if not text:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form']):
            tag.decompose()
        paragraphs = [p.get_text(' ', strip=True) for p in soup.find_all('p')]
        text = '\n'.join(p for p in paragraphs if len(p) > 40)
    return re.sub(r'[ \t]+', ' ', text).strip()[:TEXT_MAX]


def story_text(article, fetch=fetch_article_text):
    """(text, origin) to write the question from: the full article when it can
    be read, otherwise the publisher's summary and opening paragraph the
    scraper already holds. ('', reason) when there is too little of either."""
    full = fetch(article.get('url', ''))
    if len(full) >= FULL_TEXT_MIN:
        return full, 'article'
    parts = [article.get('title', ''), article.get('summary', ''), article.get('content', '')]
    fallback = '\n'.join(p.strip() for p in parts if p and p.strip())
    if len(fallback) >= FALLBACK_TEXT_MIN:
        return fallback, 'summary'
    return '', f"article text {len(full)} chars, summary {len(fallback)} chars"


def normalise(text):
    """For the quote check: quotes, dashes and whitespace made uniform, case folded."""
    text = unicodedata.normalize('NFKC', text or '')
    text = re.sub(r'[‘’‚′`]', "'", text)
    text = re.sub(r'[“”„″]', '"', text)
    text = re.sub(r'[‐-―−]', '-', text)
    return re.sub(r'\s+', ' ', text).strip().lower()


def quote_found(support, text):
    """True when the writer's supporting sentence really is in the text. A
    trailing full stop is forgiven, since models add or drop one."""
    s = normalise(support).rstrip('.')
    return len(s) >= 20 and s in normalise(text)


# ═══════════════════════════════════════════════════════════════
# PROMPTS
# ═══════════════════════════════════════════════════════════════

def writer_prompt(article, text, kind, needs):
    want = []
    if needs.get('trivia'):
        if kind == 'truefalse':
            want.append('"trivia": {"kind": "truefalse", "question": "<a statement to judge true or false>", '
                        '"answer_index": <0 if true, 1 if false>, "explanation": "<one line>", '
                        '"support": "<one exact sentence copied from the article that proves the answer>"}')
        else:
            want.append('"trivia": {"kind": "choice", "question": "...", "options": ["...", "...", "...", "..."], '
                        '"answer_index": <0-3>, "explanation": "<one line>", '
                        '"support": "<one exact sentence copied from the article that proves the answer>"}')
    if needs.get('word'):
        want.append('"joyword": [{"word": "<five letters>", "definition": "<one line, without the word>", '
                    f'"theme": "<one of {", ".join(THEMES)}>", "clue": "<crossword clue pointing at this story>"}}, '
                    '... three candidates, best first]')
    if needs.get('crossword'):
        want.append('"story_words": [{"word": "<3 to 5 letters>", "definition": "<one line, without the word>", '
                    '"clue": "<crossword clue pointing at this story>"}, ... four to six]')
    return f"""You make games for Joyscroll, a good-news app from Rally. Every game links back to the story it came from, so it should make people curious about the good news without needing them to have read it.

Story headline: {article.get('title', '')}
Publisher: {article.get('source', '')}

Article text:
\"\"\"
{text}
\"\"\"

Reply with one JSON object with these keys:
{{{', '.join(want)}}}

Rules for the trivia question:
- About the good news in this story, written fresh in plain words. Never copy a sentence of the article into the question, options or explanation.
- Fair to someone who has not read the story yet, gettable by someone who has. One option clearly right, the others plausible but wrong.
- No time-relative words (this year, currently, recently, now). No fear, blame or negative framing.
- Not answerable from general knowledge alone.
- "support" is the exception: it must be one sentence copied exactly from the article text.

Rules for words:
- Real, common English words, lowercase letters only, no names, no abbreviations.
- Positive or pleasant, and tied to what happened in the story (bloom, cheer, share, heal, plant, glow…). Nothing sad, violent, crude, medical or political.
- Clues: upbeat, under 12 words, point at the story in your own words ("What the town's new orchard did in spring"), never quote the article, never contain the answer.
"""


def checker_prompt(article, text, trivia, words):
    parts = []
    if trivia:
        opts = trivia['options']
        lines = '\n'.join(f'{i}: {o}' for i, o in enumerate(opts))
        parts.append(f"""TRIVIA — answer it from the article, then judge it:
Question: {trivia['question']}
Options:
{lines}
Return "trivia": {{"answer_index": <your answer>, "answer_stated_in_text": true/false, "exactly_one_defensible": true/false, "time_relative_wording": true/false, "negative_or_fearful": true/false, "answerable_from_general_knowledge": true/false, "reason": "<one line>"}}""")
    if words:
        parts.append(f"""WORDS — sort each into exactly one tier:
"positive" = clearly uplifting; "wholesome" = a pleasant or neutral everyday word; "excluded" = negative, sad, violent, crude, medical, political, a slur, a name, or a word most people wouldn't know.
Words: {', '.join(words)}
Return "words": {{"<word>": "<tier>", ...}}""")
    return f"""You check games for a good-news app. Be strict.

Story headline: {article.get('title', '')}

Article text:
\"\"\"
{text}
\"\"\"

{chr(10).join(parts)}

Reply with one JSON object containing the keys asked for."""


def clue_prompt(items, story_title):
    lines = '\n'.join(f'- {w} ({len(w)} letters){": " + d if d else ""}' for w, d in items)
    return f"""Write a short, upbeat crossword clue for each word below, for a mini crossword about the good-news story "{story_title}". Dictionary-style clues are fine; under 12 words; never contain the word itself or a form of it.

{lines}

Reply with one JSON object: {{"clues": {{"<word>": "<clue>", ...}}}}"""


def solver_prompt(clues):
    lines = '\n'.join(f'{i}. {c} ({n} letters)' for i, (c, n) in enumerate(clues, 1))
    return f"""Solve these crossword clues. Each answer is one common English word of the length shown.

{lines}

Reply with one JSON object: {{"answers": ["<answer 1>", "<answer 2>", ...]}} in the same order, lowercase."""


# ═══════════════════════════════════════════════════════════════
# CHECKS (pure)
# ═══════════════════════════════════════════════════════════════

def clean_trivia(raw, kind):
    """The writer's trivia, shaped and validated, or (None, reason)."""
    if not isinstance(raw, dict):
        return None, 'no trivia in the reply'
    q = str(raw.get('question') or '').strip()
    expl = str(raw.get('explanation') or '').strip()
    support = str(raw.get('support') or '').strip()
    if kind == 'truefalse':
        options = ['True', 'False']
    else:
        options = [str(o).strip() for o in (raw.get('options') or [])]
        if len(options) != 4 or any(not o for o in options) or len({o.lower() for o in options}) != 4:
            return None, 'needs four different options'
    try:
        answer = int(raw.get('answer_index'))
    except (TypeError, ValueError):
        return None, 'no answer index'
    if not 0 <= answer < len(options):
        return None, 'answer index out of range'
    if not q or not expl or len(q) > 300 or len(expl) > 300:
        return None, 'question or explanation missing or too long'
    if re.search(r'\b(this year|currently|recently|right now|nowadays)\b', q, re.I):
        return None, 'time-relative wording'
    return {'kind': kind, 'question': q, 'options': options, 'answer_index': answer,
            'explanation': expl, 'support': support}, ''


def judge_trivia(trivia, verdict):
    """'' when the checker agrees and every flag is clean, else the reason."""
    if not isinstance(verdict, dict):
        return 'checker gave no verdict'
    try:
        their = int(verdict.get('answer_index'))
    except (TypeError, ValueError):
        return 'checker gave no answer'
    if their != trivia['answer_index']:
        return f"checker answered {their}, writer {trivia['answer_index']}: {verdict.get('reason', '')}"[:300]
    must_true = ['answer_stated_in_text', 'exactly_one_defensible']
    must_false = ['time_relative_wording', 'negative_or_fearful', 'answerable_from_general_knowledge']
    for k in must_true:
        if verdict.get(k) is not True:
            return f"{k} is not true: {verdict.get('reason', '')}"[:300]
    for k in must_false:
        if verdict.get(k) is not False:
            return f"{k} is not false: {verdict.get('reason', '')}"[:300]
    return ''


def word_ok(word, dictionary, length=None):
    w = (word or '').strip().lower()
    return bool(re.fullmatch(r'[a-z]+', w)) and w in dictionary and (length is None or len(w) == length)


def clue_ok(clue, word):
    """A clue that doesn't give the answer away and isn't overlong."""
    c = (clue or '').strip()
    if not c or len(c.split()) > 14 or len(c) > 120:
        return False
    stem = word[:4] if len(word) > 4 else word
    return stem.lower() not in c.lower()


def pick_joyword(candidates, known, tiers, dictionary):
    """The first candidate that is a real 5-letter word, not excluded, and not
    already some other story's word. Returns (entry, tier) or (None, reason)."""
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        w = str(c.get('word') or '').strip().lower()
        if not word_ok(w, dictionary, 5):
            continue
        k = known.get(w)
        if k:
            if k['tier'] == 'excluded' or k['status'] != 'live' or k.get('article_id'):
                continue
            tier = k['tier']
        else:
            tier = tiers.get(w)
            if tier not in ('positive', 'wholesome'):
                continue
        return {**c, 'word': w}, tier
    return None, 'no usable 5-letter candidate (all unknown, excluded or already used)'


# ═══════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════

def api(url, key, payload, dry_run=False):
    if dry_run and payload.get('action') not in ('pending', 'known'):
        print(f"      (dry run) {payload.get('action')} → {url.rsplit('/', 1)[-1]}: "
              f"{json.dumps({k: v for k, v in payload.items() if k != 'action'})[:300]}")
        return {'success': True}
    try:
        resp = requests.post(url, json=payload, headers={'X-Api-Key': key}, timeout=60)
        data = resp.json() if resp.content else {}
        if not resp.ok:
            print(f"      ✗ {url.rsplit('/', 1)[-1]} {payload.get('action')}: {resp.status_code} {data.get('error', '')}")
        return data if resp.ok else None
    except Exception as e:
        print(f"      ✗ {url.rsplit('/', 1)[-1]} {payload.get('action')}: {type(e).__name__}: {e}")
        return None


def reject(article, stage, reason, model=None, dry_run=False):
    print(f"      ✗ {stage}: {reason}")
    api(TRIVIA_API_URL, TRIVIA_API_KEY,
        {'action': 'reject', 'article_url': article['url'], 'stage': stage, 'reason': reason[:500], 'model': model},
        dry_run)


# ═══════════════════════════════════════════════════════════════
# ONE STORY
# ═══════════════════════════════════════════════════════════════

def solve_and_fix_clues(entries, clue_for, defs, budget, title):
    """Check every clue by having the checker solve it blind, from the clue and
    the answer's length alone. Clues it gets wrong are rewritten once and
    checked again; still wrong, the word's definition stands in. Returns
    {word: clue}, or None when a word is left with no usable clue."""
    words = [e['answer'] for e in entries]

    def unsolved(ws):
        if not ws:
            return set()
        reply = call_json(CHECKER_MODEL, solver_prompt([(clue_for[w], len(w)) for w in ws]), budget, 400)
        answers = (reply or {}).get('answers')
        if not isinstance(answers, list) or len(answers) != len(ws):
            return set(ws)
        return {w for w, a in zip(ws, answers) if str(a).strip().lower() != w}

    def use_definition(w):
        d = defs.get(w, '').strip().rstrip('.')
        if clue_ok(d, w):
            clue_for[w] = d
            return True
        return False

    for w in words:
        if not clue_ok(clue_for.get(w), w):
            use_definition(w)
    wrong = unsolved([w for w in words if w in clue_for]) | {w for w in words if w not in clue_for}
    if wrong:
        fixed = call_json(WRITER_MODEL, clue_prompt([(w, defs.get(w, '')) for w in sorted(wrong)], title)
                          + '\nThe previous clues could not be solved: make each one point at exactly that word.',
                          budget, 400)
        for w, c in ((fixed or {}).get('clues') or {}).items():
            if w in wrong and clue_ok(c, w):
                clue_for[w] = c.strip()
        retry = [w for w in wrong if clue_ok(clue_for.get(w), w)]
        wrong = unsolved(retry) | (wrong - set(retry))
    for w in wrong:
        if not use_definition(w):
            return None
    return clue_for


def make_games(article, ctx, budget, rng, dry_run=False, fetch=fetch_article_text):
    """Trivia, Joyword answer and crossword for one story — whichever it still needs."""
    needs = article.get('needs') or {'trivia': True, 'word': True, 'crossword': True}
    print(f"\n  ▸ {article.get('title', '')[:70]}  (needs {', '.join(k for k, v in needs.items() if v)})")
    if not budget.can_call(4):
        print('      … model-call budget or time used up for this run')
        return False

    text, origin = story_text(article, fetch)
    if not text:
        reject(article, 'fetch', origin, dry_run=dry_run)
        return True
    print(f"      text: {len(text)} chars from the {origin}")

    kind = 'truefalse' if rng.random() < TRUE_FALSE_SHARE else 'choice'
    draft = call_json(WRITER_MODEL, writer_prompt(article, text, kind, needs), budget, 1100)
    if not draft:
        reject(article, 'writer', 'writer gave no usable reply', WRITER_MODEL, dry_run)
        return True

    trivia, why = (None, '')
    if needs.get('trivia'):
        trivia, why = clean_trivia(draft.get('trivia'), kind)
        if trivia and not quote_found(trivia['support'], text):
            trivia, why = None, 'supporting sentence is not in the article'
            reject(article, 'quote', why, WRITER_MODEL, dry_run)
        elif not trivia:
            reject(article, 'writer', why, WRITER_MODEL, dry_run)

    dictionary = ctx['dictionary']
    known = ctx['known']
    candidates = [c for c in (draft.get('joyword') or []) if isinstance(c, dict)] if needs.get('word') else []
    story_words = [c for c in (draft.get('story_words') or []) if isinstance(c, dict)] if needs.get('crossword') else []
    proposed = sorted({str(c.get('word') or '').strip().lower() for c in candidates + story_words}
                      - set(known))
    proposed = [w for w in proposed if word_ok(w, dictionary)]

    verdict = call_json(CHECKER_MODEL, checker_prompt(article, text, trivia, proposed), budget, 700) \
        if (trivia or proposed) else {}
    if verdict is None:
        verdict = {}
    tiers = {w: t for w, t in (verdict.get('words') or {}).items()
             if isinstance(w, str) and t in ('positive', 'wholesome', 'excluded')}

    # ── Trivia ──
    if trivia:
        problem = judge_trivia(trivia, verdict.get('trivia'))
        if problem:
            reject(article, 'checker', problem, CHECKER_MODEL, dry_run)
        else:
            api(TRIVIA_API_URL, TRIVIA_API_KEY, {
                'action': 'add', 'article_url': article['url'], 'kind': trivia['kind'],
                'question': trivia['question'], 'options': trivia['options'],
                'answer_index': trivia['answer_index'], 'explanation': trivia['explanation'],
                'writer_model': WRITER_MODEL, 'checker_model': CHECKER_MODEL,
            }, dry_run)
            print(f"      ✓ trivia ({trivia['kind']}): {trivia['question'][:70]}")

    # Every newly judged word goes into the bank — excluded ones too, so they
    # are never proposed again. Only the Joyword answer is linked to the story.
    joyword, tier_or_why = pick_joyword(candidates, known, tiers, dictionary) if needs.get('word') else (None, '')
    defs = {}
    for c in candidates + story_words:
        w = str(c.get('word') or '').strip().lower()
        if w and c.get('definition'):
            defs.setdefault(w, str(c['definition']).strip())
    for w, k in known.items():
        if k.get('definition'):
            defs.setdefault(w, k['definition'])
    themes = {str(c.get('word') or '').strip().lower(): c.get('theme') for c in candidates}
    new_words = [{'word': w, 'tier': t, 'theme': themes.get(w) if themes.get(w) in THEMES else None,
                  'definition': defs.get(w), 'added_by': 'job'}
                 for w, t in tiers.items() if w in proposed and (not joyword or w != joyword['word'])]
    if joyword:
        new_words.append({'word': joyword['word'], 'tier': tier_or_why,
                          'theme': joyword.get('theme') if joyword.get('theme') in THEMES else None,
                          'definition': joyword.get('definition'), 'article_url': article['url'], 'added_by': 'job'})
    if new_words:
        api(WORDS_API_URL, WORDS_API_KEY, {'action': 'add', 'words': new_words}, dry_run)
        for nw in new_words:
            known[nw['word']] = {'tier': nw['tier'], 'status': 'hidden' if nw['tier'] == 'excluded' else 'live',
                                 'definition': nw.get('definition'), 'article_id': None,
                                 'created_at': None, 'theme': nw.get('theme')}
        if joyword:
            known[joyword['word']]['article_id'] = article.get('id') or -1
    if needs.get('word'):
        if joyword:
            print(f"      ✓ Joyword: {joyword['word'].upper()}")
        else:
            reject(article, 'word', tier_or_why, WRITER_MODEL, dry_run)

    # ── Crossword ──
    if needs.get('crossword'):
        live = lambda w: known.get(w, {}).get('status') == 'live' and known[w].get('tier') in ('positive', 'wholesome')
        story_clues = {}
        for c in ([joyword] if joyword else []) + story_words + candidates:
            w = str((c or {}).get('word') or '').strip().lower()
            if w and live(w) and 3 <= len(w) <= 5 and clue_ok(c.get('clue'), w):
                story_clues.setdefault(w, c['clue'].strip())
        bank = [w for w in known if live(w) and 3 <= len(w) <= 5]
        grid = crossword.build_grid(bank, list(story_clues), ctx['recent'], rng) if story_clues else None
        if not grid:
            reject(article, 'crossword', 'no story word could be built into a grid' if story_clues
                   else 'no usable story words', dry_run=dry_run)
            return True
        entries = crossword.number_entries(grid)
        clue_for = {e['answer']: story_clues[e['answer']] for e in entries if e['answer'] in story_clues}
        others = [e['answer'] for e in entries if e['answer'] not in clue_for]
        if others:
            reply = call_json(WRITER_MODEL, clue_prompt([(w, defs.get(w, '')) for w in others],
                                                        article.get('title', '')), budget, 500)
            for w, c in ((reply or {}).get('clues') or {}).items():
                if w in others and clue_ok(c, w):
                    clue_for[w] = c.strip()
        checked = solve_and_fix_clues(entries, clue_for, defs, budget, article.get('title', ''))
        if not checked:
            reject(article, 'crossword', 'some entries have no solvable clue', CHECKER_MODEL, dry_run)
            return True
        clues = [{'num': e['num'], 'dir': e['dir'], 'row': e['row'], 'col': e['col'], 'clue': checked[e['answer']],
                  **({'article_url': article['url']} if e['answer'] in story_clues else {})}
                 for e in entries]
        api(CROSSWORDS_API_URL, WORDS_API_KEY, {'action': 'add', 'article_url': article['url'],
                                               'size': len(grid), 'grid': grid, 'clues': clues}, dry_run)
        print(f"      ✓ crossword {len(grid)}×{len(grid)}: {' / '.join(grid)}")
    return True


# ═══════════════════════════════════════════════════════════════
# THE RUN
# ═══════════════════════════════════════════════════════════════

def run(dry_run=False, limit=None):
    """Make the games recent stories are missing. Safe to call at the end of
    every scrape: it does nothing (and says so) when it isn't configured."""
    print("\n" + "─" * 60)
    print("JOYSCROLL GAMES")
    missing = [n for n, v in (('OPENROUTER_API_KEY', OPENROUTER_API_KEY), ('NEWS_API_URL', NEWS_API_URL),
                              ('JOYSCROLL_WORDS_API_KEY', WORDS_API_KEY),
                              ('JOYSCROLL_TRIVIA_API_KEY', TRIVIA_API_KEY)) if not v]
    if missing:
        print(f"  Skipped: {', '.join(missing)} not set")
        return {'stories': 0, 'calls': 0, 'skipped': True}

    pending = api(TRIVIA_API_URL, TRIVIA_API_KEY, {'action': 'pending', 'hours': 48, 'limit': 40})
    articles = (pending or {}).get('articles') or []
    if limit is not None:
        articles = articles[:limit]
    print(f"  {len(articles)} recent stories are missing a game")
    if not articles:
        return {'stories': 0, 'calls': 0}

    known_rows = (api(WORDS_API_URL, WORDS_API_KEY, {'action': 'known'}) or {}).get('words') or []
    known = {r['word']: r for r in known_rows if isinstance(r, dict) and r.get('word')}
    # Words from the last fortnight's stories are tried first in every grid.
    cutoff = time.strftime('%Y-%m-%d', time.gmtime(time.time() - STORY_WORD_DAYS * 86400))
    recent = {w for w, r in known.items() if r.get('article_id') and str(r.get('created_at') or '') >= cutoff}
    live_bank = sum(1 for r in known.values() if r.get('status') == 'live' and r.get('tier') != 'excluded')
    print(f"  Word bank: {len(known)} known, {live_bank} live")
    if live_bank < 1500:
        print("  ⚠ The bank is small — run the word seed (Actions ▸ Joyscroll word seed) or grids will often fail")

    ctx = {'dictionary': load_dictionary(), 'known': known, 'recent': recent}
    budget = Budget()
    rng = random.Random()
    done = 0
    for article in articles:
        if not make_games(article, ctx, budget, rng, dry_run):
            break
        done += 1
    print(f"\n  Joyscroll games: {done} stories worked, {budget.calls} model calls "
          f"(cap {budget.max_calls})")
    return {'stories': done, 'calls': budget.calls}


if __name__ == '__main__':
    args = sys.argv[1:]
    lim = None
    if '--limit' in args:
        lim = int(args[args.index('--limit') + 1])
    run(dry_run='--dry-run' in args, limit=lim)
