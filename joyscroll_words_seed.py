"""Seed Joyscroll's word bank from the ENABLE list — run by hand, once.

Every 3–5 letter word in words/enable-3-5.txt that the bank doesn't know yet is
sorted by a model, about a hundred at a time, into:
  positive  — clearly uplifting
  wholesome — a pleasant or neutral everyday word
  excluded  — negative, crude, sad, violent, medical, political, a slur, a
              name, or obscure
with a theme and a one-line definition, and posted to api/words.php — the
excluded ones too, hidden, so nothing proposes them again. Crossjoy fills its
grids from the first two tiers; the definitions are the fallback clues.

Resumable: words already in the bank are skipped, so a run that hits its cap
(or fails) is simply run again. About 135 calls for the whole list.

    python joyscroll_words_seed.py --max-calls 40
    python joyscroll_words_seed.py --dry-run --max-calls 1   # print one batch, post nothing

Needs OPENROUTER_API_KEY, NEWS_API_URL and JOYSCROLL_WORDS_API_KEY. The model
is JOYSCROLL_SEED_MODEL (default the trivia writer's).
"""

import os
import sys
from collections import Counter

import joyscroll_games as games

SEED_MODEL = os.environ.get('JOYSCROLL_SEED_MODEL') or games.WRITER_MODEL
BATCH = 100


def classify_prompt(words):
    return f"""You are building the word bank for Joyscroll, a good-news app's word games (a mini crossword and a 5-letter word-guessing game). Sort each word into one tier:

"positive"  = clearly uplifting (bloom, cheer, kind, smile)
"wholesome" = a pleasant or neutral everyday word most adults know (table, river, salt, walk)
"excluded"  = negative, sad, crude, violent, medical, political, religious, a slur, a name or brand, an abbreviation, or a word most adults wouldn't know (gloom, scab, ebon, yurt-style obscure Scrabble words)

When unsure between wholesome and excluded, choose excluded.

For positive and wholesome words also give a theme (one of {', '.join(games.THEMES)}) and a one-line definition under 15 words that does not contain the word.

Words: {', '.join(words)}

Reply with one JSON object: {{"words": {{"<word>": {{"tier": "...", "theme": "...", "definition": "..."}}, ...}}}} with every word above."""


def parse_batch(reply, words):
    """The model's verdicts for this batch, as rows for words.php. Words the
    reply left out are simply not posted, and are retried on the next run."""
    out = []
    verdicts = (reply or {}).get('words') or {}
    for w in words:
        v = verdicts.get(w)
        if not isinstance(v, dict) or v.get('tier') not in ('positive', 'wholesome', 'excluded'):
            continue
        row = {'word': w, 'tier': v['tier'], 'added_by': 'seed'}
        if v['tier'] != 'excluded':
            theme = str(v.get('theme') or '').strip().lower()
            if theme in games.THEMES:
                row['theme'] = theme
            definition = str(v.get('definition') or '').strip()
            if definition and w not in definition.lower():
                row['definition'] = definition[:200]
        out.append(row)
    return out


def main(max_calls=40, dry_run=False):
    for name, value in (('OPENROUTER_API_KEY', games.OPENROUTER_API_KEY), ('NEWS_API_URL', games.NEWS_API_URL),
                        ('JOYSCROLL_WORDS_API_KEY', games.WORDS_API_KEY)):
        if not value:
            print(f"✗ {name} is not set")
            return 1

    known = (games.api(games.WORDS_API_URL, games.WORDS_API_KEY, {'action': 'known'}) or {}).get('words')
    if known is None:
        print("✗ Could not read the word bank (api/words.php action=known)")
        return 1
    have = {r['word'] for r in known}
    todo = sorted(games.load_dictionary() - have)
    print(f"Word bank knows {len(have)} words; {len(todo)} ENABLE words still to sort "
          f"(~{-(-len(todo) // BATCH)} calls). This run: up to {max_calls} calls of {BATCH}.")

    budget = games.Budget(max_calls=max_calls, seconds=5 * 3600)
    tally = Counter()
    for i in range(0, len(todo), BATCH):
        if not budget.can_call():
            break
        words = todo[i:i + BATCH]
        rows = parse_batch(games.call_json(SEED_MODEL, classify_prompt(words), budget, max_tokens=6000, timeout=120),
                           words)
        if not rows:
            print(f"  ✗ batch {words[0]}…{words[-1]}: no usable reply")
            continue
        games.api(games.WORDS_API_URL, games.WORDS_API_KEY, {'action': 'add', 'words': rows}, dry_run)
        for r in rows:
            tally[(r['tier'], len(r['word']))] += 1
        print(f"  ✓ {words[0]}…{words[-1]}: {len(rows)}/{len(words)} sorted")

    print("\nSorted this run, by tier and length:")
    for tier in ('positive', 'wholesome', 'excluded'):
        counts = ', '.join(f"{n} letters: {tally[(tier, n)]}" for n in (3, 4, 5))
        print(f"  {tier:9} {counts}")
    left = len(todo) - sum(tally.values())
    print(f"\nSeed: {sum(tally.values())} words sorted in {budget.calls} calls; about {left} still to go"
          + (" — run it again." if left > 0 else "."))
    return 0


if __name__ == '__main__':
    args = sys.argv[1:]
    cap = int(args[args.index('--max-calls') + 1]) if '--max-calls' in args else 40
    sys.exit(main(max_calls=cap, dry_run='--dry-run' in args))
