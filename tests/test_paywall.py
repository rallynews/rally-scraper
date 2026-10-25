"""At least half of what a run publishes must be free to read
(editorial_filter.limit_paywalled). Run with:  python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import editorial_filter  # noqa: E402

PAID = {'Paywall Times', 'Locked Post'}
paid = lambda a: a['source'] in PAID


def story(title, source, score, category='world'):
    return {'title': title, 'source': source, 'positivity_score': score, 'category': category}


class LimitPaywalledTests(unittest.TestCase):
    def run_rule(self, published, unused=(), target=6, **kw):
        return editorial_filter.limit_paywalled(published, list(unused), paid, target, **kw)

    def test_leaves_a_run_alone_when_half_or_more_is_free(self):
        pub = [story('a', 'Paywall Times', 9), story('b', 'Free Daily', 8)]
        out, unused = self.run_rule(pub)
        self.assertEqual([a['title'] for a in out], ['a', 'b'])
        self.assertEqual(unused, [])

    def test_drops_the_weakest_paywalled_stories_until_half_are_free(self):
        pub = [story('p1', 'Paywall Times', 9.5), story('p2', 'Locked Post', 8), story('p3', 'Paywall Times', 7.6),
               story('f1', 'Free Daily', 7.8)]
        out, unused = self.run_rule(pub, target=4)
        self.assertEqual(sorted(a['title'] for a in out), ['f1', 'p1'])
        self.assertEqual(sorted(a['title'] for a in unused), ['p2', 'p3'])

    def test_tops_up_with_free_stories_that_were_set_aside(self):
        pub = [story('p1', 'Paywall Times', 9), story('p2', 'Locked Post', 8.5), story('f1', 'Free Daily', 8)]
        spare = [story('f2', 'Free Daily', 6.8), story('p3', 'Paywall Times', 6.9)]
        out, unused = self.run_rule(pub, spare, target=4)
        self.assertEqual(sorted(a['title'] for a in out), ['f1', 'f2', 'p1', 'p2'])
        self.assertEqual([a['title'] for a in unused], ['p3'])

    def test_top_up_respects_the_category_cap(self):
        pub = [story('p1', 'Paywall Times', 9, 'arts'), story('p2', 'Locked Post', 8.5, 'arts'),
               story('f1', 'Free Daily', 8, 'climate')]
        spare = [story('f2', 'Free Daily', 7, 'climate'), story('f3', 'Free Daily', 6.6, 'ai')]
        out, _ = self.run_rule(pub, spare, target=4, category_key='category', per_category=1)
        titles = sorted(a['title'] for a in out)
        self.assertNotIn('f2', titles)  # climate already has f1
        free = [a for a in out if not paid(a)]
        self.assertGreaterEqual(len(free) * 2, len(out))

    def test_publishes_fewer_rather_than_break_the_rule(self):
        pub = [story('p1', 'Paywall Times', 9), story('p2', 'Locked Post', 8.8), story('p3', 'Paywall Times', 8.7)]
        out, _ = self.run_rule(pub, target=3)
        self.assertEqual(out, [])

    def test_order_is_strongest_first(self):
        pub = [story('f1', 'Free Daily', 7), story('p1', 'Paywall Times', 9)]
        spare = [story('f2', 'Free Daily', 8)]
        out, _ = self.run_rule(pub, spare, target=3)
        self.assertEqual([a['title'] for a in out], ['p1', 'f2', 'f1'])


if __name__ == '__main__':
    unittest.main()
