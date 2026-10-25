"""Tests for Joyscroll's games: the crossword builder and validator, the
checks on what the models write, and one whole story with the models and the
API faked. Run with:  python -m unittest discover tests
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import joyscroll_crossword as cw  # noqa: E402
import joyscroll_games as games  # noqa: E402

DICT = games.load_dictionary()


class GridTests(unittest.TestCase):
    def test_every_pattern_has_only_long_enough_entries(self):
        for p in cw.PATTERNS_5 + [cw.PATTERN_4]:
            self.assertTrue(all(n >= cw.MIN_ENTRY for *_, n in cw.entries(p)), p)

    def test_patterns_are_symmetric(self):
        for p in cw.PATTERNS_5:
            flipped = [row[::-1] for row in reversed(p)]
            self.assertEqual(p, flipped)

    def test_numbering_matches_the_design_documents_grid(self):
        grid = ['##ski', '#spin', 'thank', 'word#', 'owe##']
        e = {(x['num'], x['dir']): x['answer'] for x in cw.number_entries(grid)}
        self.assertEqual(e[(1, 'across')], 'ski')
        self.assertEqual(e[(4, 'across')], 'spin')
        self.assertEqual(e[(5, 'across')], 'thank')
        self.assertEqual(e[(1, 'down')], 'spare')
        self.assertEqual(e[(3, 'down')], 'ink')
        self.assertEqual(e[(5, 'down')], 'two')
        self.assertEqual(len(e), 10)

    def test_validator_accepts_a_good_grid(self):
        grid = ['##ski', '#spin', 'thank', 'word#', 'owe##']
        words = {x['answer'] for x in cw.number_entries(grid)}
        self.assertEqual(cw.validate_grid(grid, words), [])

    def test_validator_rejects_unknown_words_repeats_and_bad_shapes(self):
        grid = ['##ski', '#spin', 'thank', 'word#', 'owe##']
        words = {x['answer'] for x in cw.number_entries(grid)} - {'ink'}
        self.assertTrue(any("'ink'" in p for p in cw.validate_grid(grid, words)))
        self.assertTrue(cw.validate_grid(['abc', 'def', 'ghi'], set()))
        self.assertTrue(cw.validate_grid(['ab1d', 'efgh', 'ijkl', 'mnop'], set()))
        square = ['card', 'area', 'rear', 'dart']  # every word twice
        self.assertTrue(any('twice' in p for p in cw.validate_grid(square, {'card', 'area', 'rear', 'dart'})))

    def test_fill_builds_a_valid_grid_around_the_story_word(self):
        rng = random.Random(7)
        bank = [w for w in DICT if rng.random() < 0.5]
        grid = cw.build_grid(bank, ['bloom'], rng=rng)
        self.assertIsNotNone(grid)
        answers = [x['answer'] for x in cw.number_entries(grid)]
        self.assertIn('bloom', answers)
        self.assertEqual(cw.validate_grid(grid, set(bank) | {'bloom'}), [])

    def test_fill_gives_up_cleanly_when_impossible(self):
        self.assertIsNone(cw.build_grid(['cat', 'dog'], ['bloom'], rng=random.Random(1)))
        self.assertIsNone(cw.build_grid(['cat'], [], rng=random.Random(1)))


class CheckTests(unittest.TestCase):
    def test_quote_check_forgives_typography_not_wording(self):
        text = 'Neighbours planted 40 fruit trees where cars used to idle — and the harvest is “shared”.'
        self.assertTrue(games.quote_found('neighbours planted 40 fruit trees where cars used to idle - and the harvest is "shared"', text))
        self.assertFalse(games.quote_found('Neighbours planted 50 fruit trees where cars used to idle', text))
        self.assertFalse(games.quote_found('trees', text))

    def test_clean_trivia(self):
        ok, _ = games.clean_trivia({'question': 'What did they plant?', 'options': ['A', 'B', 'C', 'D'],
                                    'answer_index': 1, 'explanation': 'Trees.', 'support': 'x'}, 'choice')
        self.assertEqual(ok['options'], ['A', 'B', 'C', 'D'])
        bad, why = games.clean_trivia({'question': 'Q?', 'options': ['A', 'A', 'C', 'D'], 'answer_index': 0,
                                       'explanation': 'x'}, 'choice')
        self.assertIsNone(bad)
        tf, _ = games.clean_trivia({'question': 'They planted trees.', 'answer_index': 0, 'explanation': 'Yes.'},
                                   'truefalse')
        self.assertEqual(tf['options'], ['True', 'False'])
        timely, why = games.clean_trivia({'question': 'What opened this year?', 'answer_index': 0,
                                          'explanation': 'x'}, 'truefalse')
        self.assertIsNone(timely)
        self.assertIn('time', why)

    def test_judge_trivia(self):
        t = {'answer_index': 1}
        clean = {'answer_index': 1, 'answer_stated_in_text': True, 'exactly_one_defensible': True,
                 'time_relative_wording': False, 'negative_or_fearful': False,
                 'answerable_from_general_knowledge': False}
        self.assertEqual(games.judge_trivia(t, clean), '')
        self.assertIn('checker answered', games.judge_trivia(t, {**clean, 'answer_index': 2}))
        self.assertIn('negative', games.judge_trivia(t, {**clean, 'negative_or_fearful': True}))
        self.assertIn('answer_stated', games.judge_trivia(t, {**clean, 'answer_stated_in_text': None}))

    def test_clue_ok(self):
        self.assertTrue(games.clue_ok('What the orchard did in spring', 'bloom'))
        self.assertFalse(games.clue_ok('Blooming lovely', 'bloom'))
        self.assertFalse(games.clue_ok('', 'bloom'))

    def test_pick_joyword_skips_used_excluded_and_made_up_words(self):
        known = {
            'cheer': {'tier': 'positive', 'status': 'live', 'article_id': 9},     # another story's
            'gloom': {'tier': 'excluded', 'status': 'hidden', 'article_id': None},
            'share': {'tier': 'wholesome', 'status': 'live', 'article_id': None},  # a seed word, free
        }
        cands = [{'word': 'cheer'}, {'word': 'gloom'}, {'word': 'zzzzz'}, {'word': 'share'}]
        pick, tier = games.pick_joyword(cands, known, {}, DICT)
        self.assertEqual((pick['word'], tier), ('share', 'wholesome'))
        pick, tier = games.pick_joyword([{'word': 'bloom'}], {}, {'bloom': 'positive'}, DICT)
        self.assertEqual((pick['word'], tier), ('bloom', 'positive'))
        pick, why = games.pick_joyword([{'word': 'bloom'}], {}, {'bloom': 'excluded'}, DICT)
        self.assertIsNone(pick)

    def test_parse_json_reply(self):
        self.assertEqual(games.parse_json_reply('```json\n{"a": 1}\n```'), {'a': 1})
        self.assertEqual(games.parse_json_reply('Sure! {"a": 2} hope that helps'), {'a': 2})
        self.assertIsNone(games.parse_json_reply('no json'))


def shift(word, by=1):
    """The fake models' code for an answer inside a clue: each letter moved
    along the alphabet, so the fake solver can read it back and the clue never
    contains the word itself."""
    return ''.join(chr((ord(c) - 97 + by) % 26 + 97) for c in word)


ARTICLE_TEXT = (
    'A small town turned its empty car park into a community orchard. '
    'Neighbours planted forty fruit trees where cars used to idle. '
    'The first harvest was shared with every family on the street, and local children now climb the apple trees after school. '
) * 6


class WholeStoryTest(unittest.TestCase):
    """One story through make_games, with the models and the API faked."""

    def setUp(self):
        self.posted = []
        self._api, self._call = games.api, games.call_json
        self._urls = (games.WORDS_API_URL, games.CROSSWORDS_API_URL, games.TRIVIA_API_URL)
        games.WORDS_API_URL, games.CROSSWORDS_API_URL, games.TRIVIA_API_URL = (
            'https://api.test/words.php', 'https://api.test/crosswords.php', 'https://api.test/trivia.php')
        rng = random.Random(3)
        bank = [w for w in DICT if rng.random() < 0.5]
        self.known = {w: {'tier': 'wholesome', 'status': 'live', 'article_id': None, 'created_at': None,
                          'definition': f'meaning of {shift(w)}'} for w in bank}
        for w in ('bloom', 'tree', 'gloom'):
            self.known.pop(w, None)

        def fake_api(url, key, payload, dry_run=False):
            self.posted.append((url.rsplit('/', 1)[-1], payload))
            return {'success': True}

        def fake_call(model, prompt, budget, max_tokens=900, timeout=45):
            budget.spend()
            if 'Solve these crossword clues' in prompt:
                # The fake solver gets every clue right: the clue texts below
                # all end with the answer in shift() code, which it reads back.
                import re
                lines = re.findall(r'^\d+\. (.*) \((\d+) letters\)$', prompt, re.M)
                return {'answers': [shift(clue.split()[-1], -1) for clue, _ in lines]}
            if 'Write a short, upbeat crossword clue' in prompt:
                import re
                words = re.findall(r'^- ([a-z]+) \(', prompt, re.M)
                return {'clues': {w: f'Clue for {shift(w)}' for w in words}}
            if 'You check games' in prompt:
                return {'trivia': {'answer_index': 1, 'answer_stated_in_text': True, 'exactly_one_defensible': True,
                                   'time_relative_wording': False, 'negative_or_fearful': False,
                                   'answerable_from_general_knowledge': False},
                        'words': {'bloom': 'positive', 'tree': 'wholesome', 'gloom': 'excluded'}}
            return {
                'trivia': {'kind': 'choice', 'question': 'What did the town plant in its old car park?',
                           'options': ['Wildflowers', 'Fruit trees', 'A maze', 'Sunflowers'], 'answer_index': 1,
                           'explanation': 'Neighbours planted fruit trees where cars once idled.',
                           'support': 'Neighbours planted forty fruit trees where cars used to idle.'},
                'joyword': [{'word': 'bloom', 'definition': 'To flower.', 'theme': 'nature',
                             'clue': 'What the orchard did in ' + shift('bloom')}],
                'story_words': [{'word': 'tree', 'definition': 'A tall plant.', 'clue': 'Forty went in, ' + shift('tree')},
                                {'word': 'gloom', 'clue': 'x ' + shift('gloom')}],
            }

        games.api, games.call_json = fake_api, fake_call

    def tearDown(self):
        games.api, games.call_json = self._api, self._call
        games.WORDS_API_URL, games.CROSSWORDS_API_URL, games.TRIVIA_API_URL = self._urls

    def test_one_story_gets_all_three(self):
        article = {'id': 1, 'url': 'https://ex.com/orchard', 'title': 'Town plants orchard', 'source': 'Src',
                   'needs': {'trivia': True, 'word': True, 'crossword': True}}
        ctx = {'dictionary': DICT, 'known': self.known, 'recent': set()}
        orig_kind = games.TRUE_FALSE_SHARE
        games.TRUE_FALSE_SHARE = 0  # force a 4-option question
        try:
            games.make_games(article, ctx, games.Budget(20, 600), random.Random(5),
                             fetch=lambda url: ARTICLE_TEXT)
        finally:
            games.TRUE_FALSE_SHARE = orig_kind
        by = {}
        for endpoint, payload in self.posted:
            by.setdefault((endpoint, payload['action']), []).append(payload)
        self.assertNotIn(('trivia.php', 'reject'), by, by.get(('trivia.php', 'reject')))
        trivia = by[('trivia.php', 'add')][0]
        self.assertEqual(trivia['options'][trivia['answer_index']], 'Fruit trees')
        added = {w['word']: w for w in by[('words.php', 'add')][0]['words']}
        self.assertEqual(added['bloom']['article_url'], 'https://ex.com/orchard')
        self.assertEqual(added['gloom']['tier'], 'excluded')
        self.assertNotIn('article_url', added['tree'])
        grid_post = by[('crosswords.php', 'add')][0]
        bank = {w for w, k in self.known.items() if k['status'] == 'live' and k['tier'] != 'excluded'}
        self.assertEqual(cw.validate_grid(grid_post['grid'], bank), [])
        answers = {(e['dir'], e['row'], e['col']): e['answer'] for e in cw.number_entries(grid_post['grid'])}
        story = {e['answer'] for e in cw.number_entries(grid_post['grid'])} & {'bloom', 'tree'}
        self.assertTrue(story, 'the grid is built around a story word')
        for clue in grid_post['clues']:
            answer = answers[(clue['dir'], clue['row'], clue['col'])]
            self.assertEqual('article_url' in clue, answer in {'bloom', 'tree'})
            if answer == 'bloom':
                self.assertEqual(clue['clue'], 'What the orchard did in ' + shift('bloom'))
        self.assertNotIn('children now climb', str(by))  # article text never posted

    def test_unreadable_story_is_rejected_at_fetch(self):
        article = {'id': 2, 'url': 'https://ex.com/x', 'title': 'T', 'summary': 'short',
                   'needs': {'trivia': True, 'word': True, 'crossword': True}}
        ctx = {'dictionary': DICT, 'known': self.known, 'recent': set()}
        games.make_games(article, ctx, games.Budget(20, 600), random.Random(1), fetch=lambda url: '')
        self.assertEqual([(e, p['action'], p['stage']) for e, p in self.posted], [('trivia.php', 'reject', 'fetch')])


if __name__ == '__main__':
    unittest.main()
