"""Crossjoy grids: building and checking Joyscroll's mini crosswords.

Pure code, no model. A grid is a list of row strings, '#' for a block and a
lowercase letter for a filled square. Every run of two or more white squares,
across or down, is an entry, and every entry must be a word from the bank
it was filled from, with no word used twice in one grid.

The fill is a backtracking search over a handful of block patterns (5×5,
and an open 4×4 as the fallback), always starting from one of the story's
own words, so every grid is about the story it was built for. Words from
recent stories are tried before the general bank.
"""

import random

# 5×5 block patterns, each the same turned through 180°, as minis usually are,
# and each with every entry 3+ long. The first is the design document's grid.
PATTERNS_5 = [
    ['##...', '#....', '.....', '....#', '...##'],
    ['...##', '....#', '.....', '#....', '##...'],
    ['#....', '.....', '.....', '.....', '....#'],
    ['....#', '.....', '.....', '.....', '#....'],
    ['#...#', '.....', '.....', '.....', '#...#'],
]
PATTERN_4 = ['....', '....', '....', '....']

MIN_ENTRY = 3


def entries(pattern):
    """Every entry of a pattern as (dir, row, col, length), across first."""
    size = len(pattern)
    out = []
    for r in range(size):
        c = 0
        while c < size:
            if pattern[r][c] == '#':
                c += 1
                continue
            start = c
            while c < size and pattern[r][c] != '#':
                c += 1
            if c - start >= 2:
                out.append(('across', r, start, c - start))
    for c in range(size):
        r = 0
        while r < size:
            if pattern[r][c] == '#':
                r += 1
                continue
            start = r
            while r < size and pattern[r][c] != '#':
                r += 1
            if r - start >= 2:
                out.append(('down', start, c, r - start))
    return out


def cells(entry):
    d, r, c, n = entry
    return [(r, c + i) if d == 'across' else (r + i, c) for i in range(n)]


def number_entries(grid):
    """Number a grid the usual way: left to right, top to bottom, one number
    per square that starts an across or a down entry. Returns
    [{num, dir, row, col, answer}] in clue order (across, then down)."""
    size = len(grid)
    white = lambda r, c: 0 <= r < size and 0 <= c < size and grid[r][c] != '#'
    nums = {}
    n = 0
    for r in range(size):
        for c in range(size):
            if not white(r, c):
                continue
            starts_across = not white(r, c - 1) and white(r, c + 1)
            starts_down = not white(r - 1, c) and white(r + 1, c)
            if starts_across or starts_down:
                n += 1
                nums[(r, c)] = n
    out = []
    for d, r, c, length in entries(grid):
        word = ''.join(grid[rr][cc] for rr, cc in cells((d, r, c, length)))
        out.append({'num': nums[(r, c)], 'dir': d, 'row': r, 'col': c, 'answer': word})
    out.sort(key=lambda e: (e['dir'] != 'across', e['num']))
    return out


def validate_grid(grid, allowed):
    """Return a list of problems with a filled grid (empty = valid).

    Checks it is square and 4×4 or 5×5, that every square is a block or a
    letter, that every entry is MIN_ENTRY+ long and a word in `allowed`, and
    that no word appears twice."""
    problems = []
    size = len(grid)
    if size not in (4, 5) or any(len(row) != size for row in grid):
        return [f'grid must be 4×4 or 5×5, got {size} rows of {[len(r) for r in grid]}']
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
            if ch != '#' and not ('a' <= ch <= 'z'):
                problems.append(f'square {r},{c} is {ch!r}, not a letter or a block')
    if problems:
        return problems
    seen = set()
    for e in number_entries(grid):
        word = e['answer']
        if len(word) < MIN_ENTRY:
            problems.append(f"{e['num']} {e['dir']} '{word}' is shorter than {MIN_ENTRY}")
        elif word not in allowed:
            problems.append(f"{e['num']} {e['dir']} '{word}' is not in the word bank")
        if word in seen:
            problems.append(f"'{word}' appears twice")
        seen.add(word)
    if not seen:
        problems.append('grid has no entries')
    return problems


class WordIndex:
    """Words by length, and by (length, position, letter), for fast lookups of
    what fits a partly filled slot."""

    def __init__(self, words):
        self.by_len = {}
        self.by_pos = {}
        for w in set(words):
            self.by_len.setdefault(len(w), set()).add(w)
            for i, ch in enumerate(w):
                self.by_pos.setdefault((len(w), i, ch), set()).add(w)

    def matching(self, pattern):
        """Words fitting a slot pattern like 'b?o?m' ('?' = open)."""
        n = len(pattern)
        result = None
        for i, ch in enumerate(pattern):
            if ch == '?':
                continue
            hits = self.by_pos.get((n, i, ch), set())
            result = hits if result is None else result & hits
            if not result:
                return set()
        return set(self.by_len.get(n, set())) if result is None else result


def fill(pattern, index, seed_word, preferred=(), rng=None, max_steps=20000):
    """Fill `pattern` with words from `index`, with `seed_word` in one of its
    entries. Returns the grid as row strings, or None if no fill was found
    within `max_steps` (the search gives up rather than run long).

    `preferred` words (recent stories' words) are tried first in every slot;
    the rest of each slot's candidates are shuffled, so the same bank gives
    different grids."""
    rng = rng or random.Random()
    slots = [e for e in entries(pattern)]
    if any(n < MIN_ENTRY for _, _, _, n in slots):
        return None
    preferred = set(preferred)
    steps = [0]

    def grid_from(letters):
        size = len(pattern)
        return [''.join('#' if pattern[r][c] == '#' else letters.get((r, c), '?') for c in range(size))
                for r in range(size)]

    def slot_pattern(slot, letters):
        return ''.join(letters.get(p, '?') for p in cells(slot))

    def solve(letters, open_slots, used):
        steps[0] += 1
        if steps[0] > max_steps:
            return None
        if not open_slots:
            return dict(letters)
        # Most constrained slot first.
        best, best_cands = None, None
        for slot in open_slots:
            cands = index.matching(slot_pattern(slot, letters)) - used
            if not cands:
                return None
            if best is None or len(cands) < len(best_cands):
                best, best_cands = slot, cands
        ordered = sorted(best_cands & preferred) + rng.sample(sorted(best_cands - preferred), len(best_cands - preferred))
        rest = [s for s in open_slots if s != best]
        for word in ordered:
            placed = dict(letters)
            for p, ch in zip(cells(best), word):
                placed[p] = ch
            done = solve(placed, rest, used | {word})
            if done is not None:
                return done
            if steps[0] > max_steps:
                return None
        return None

    seed = seed_word.lower()
    seed_slots = [s for s in slots if s[3] == len(seed)]
    rng.shuffle(seed_slots)
    for slot in seed_slots:
        letters = {p: ch for p, ch in zip(cells(slot), seed)}
        solved = solve(letters, [s for s in slots if s != slot], {seed})
        if solved is not None:
            grid = grid_from(solved)
            if '?' not in ''.join(grid):
                return grid
        if steps[0] > max_steps:
            break
    return None


def build_grid(bank_words, story_words, preferred=(), rng=None):
    """Build one grid around a story.

    Tries each story word (longest first) as the seed, across the 5×5
    patterns in random order, then the open 4×4. The story's own words are
    added to what may be used, so a story word missing from the bank can still
    seed its grid. Returns the grid, or None when nothing fills."""
    rng = rng or random.Random()
    story = sorted({w.lower() for w in story_words if MIN_ENTRY <= len(w) <= 5 and w.isalpha()},
                   key=len, reverse=True)
    if not story:
        return None
    index = WordIndex(set(bank_words) | set(story))
    prefer = set(preferred) | set(story)
    patterns = PATTERNS_5[:]
    rng.shuffle(patterns)
    for seed in story:
        for pattern in patterns + [PATTERN_4]:
            if not any(n == len(seed) for _, _, _, n in entries(pattern)):
                continue
            grid = fill(pattern, index, seed, prefer, rng)
            if grid:
                return grid
    return None
