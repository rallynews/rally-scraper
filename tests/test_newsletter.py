"""Bright Spots themed sections and Around The World. Run with:
python -m unittest discover tests
"""

import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import newsletter  # noqa: E402


def art(title, category="world", summary="A nice thing happened.", region=None, **kw):
    a = {"title": title, "category": category, "summary": summary,
         "url": f"https://example.com/{title.replace(' ', '-')}", "source": "Example"}
    if region:
        a["region"] = region
    a.update(kw)
    return a


NEWS = [
    art("Puppy rescued from well", "entertainment", "A dog was saved by firefighters."),
    art("Couple marries after 60 years", "entertainment", "Love story of high school sweethearts."),
    art("Wind power record in Denmark", "climate", "Renewable energy hits new high.", region="Europe"),
    art("Senate passes bipartisan bill", "politics", "A new law gets broad support.", region="North America"),
    art("Museum opens craft exhibit", "arts", "Pottery and knitting on show."),
    art("Kenya plants a million trees", "climate", "Forest restoration.", region="Africa"),
    art("Japan robot helps elderly", "ai", "Tech innovation in care homes.", region="Asia"),
    art("Brazil reef recovers", "climate", "Coral reef restoration.", region="South America"),
    art("Kiwi bird numbers rise", "climate", "Wildlife in New Zealand.", region="Oceania"),
    art("Original piece", "world", "Staff written.", rally_originals="1"),
]


def failing_ai(*a, **k):
    raise RuntimeError("no network")


class ThemedTests(unittest.TestCase):
    def test_fallback_picks_three_distinct_articles(self):
        with mock.patch.object(newsletter, "ai", failing_ai):
            out = newsletter.pick_themed(NEWS, exclude=set(), rng=random.Random(1))
        self.assertEqual(len(out), 3)
        self.assertEqual(len({t for t, _ in out}), 3)
        self.assertEqual(len({id(a) for _, a in out}), 3)
        for theme, _ in out:
            self.assertIn(theme, newsletter.THEMES)
        self.assertNotIn("Original piece", [a["title"] for _, a in out])

    def test_ai_picks_are_used(self):
        rng = random.Random(3)
        chosen = random.Random(3).sample(list(newsletter.THEMES), len(newsletter.THEMES))[:3]
        reply = '{"%s": 1, "%s": 2, "%s": 5}' % tuple(chosen)
        with mock.patch.object(newsletter, "ai", lambda *a, **k: reply):
            out = newsletter.pick_themed(NEWS, exclude=set(), rng=rng)
        self.assertEqual([t for t, _ in out], chosen)
        self.assertEqual([a["title"] for _, a in out],
                         ["Puppy rescued from well", "Couple marries after 60 years",
                          "Museum opens craft exhibit"])

    def test_excluded_articles_are_skipped(self):
        exclude = {a["url"] for a in NEWS[:5]}
        with mock.patch.object(newsletter, "ai", failing_ai):
            out = newsletter.pick_themed(NEWS, exclude=exclude, rng=random.Random(2))
        for _, a in out:
            self.assertNotIn(a["url"], exclude)


class WorldTests(unittest.TestCase):
    def test_stored_regions_fill_every_region_without_ai(self):
        with mock.patch.object(newsletter, "ai", failing_ai):
            out = newsletter.pick_world(NEWS, exclude=set())
        self.assertEqual([r for r, _ in out], list(newsletter.REGIONS))
        for region, a in out:
            self.assertEqual(a["region"], region)

    def test_ai_cannot_contradict_stored_region(self):
        # AI puts the Denmark (Europe) story under Asia; it must be rejected and
        # Asia filled from the stored region instead.
        with mock.patch.object(newsletter, "ai", lambda *a, **k: '{"Asia": 3, "Europe": 3}'):
            out = dict(newsletter.pick_world(NEWS, exclude=set()))
        self.assertEqual(out["Asia"]["title"], "Japan robot helps elderly")
        self.assertEqual(out["Europe"]["title"], "Wind power record in Denmark")

    def test_ai_assigns_untagged_stories(self):
        news = [art("Story in Peru"), art("Story in India")]
        with mock.patch.object(newsletter, "ai",
                               lambda *a, **k: '{"South America": 1, "Asia": 2, "Europe": null}'):
            out = newsletter.pick_world(news, exclude=set())
        self.assertEqual([(r, a["title"]) for r, a in out],
                         [("South America", "Story in Peru"), ("Asia", "Story in India")])


class HtmlTests(unittest.TestCase):
    def test_sections_sit_between_cry_and_friends(self):
        themed = [("Stories of Love", NEWS[1])]
        world = [("Europe", NEWS[2])]
        cry = {"content": "Good things today.", "stories": []}
        friends = [{"name": "Friend", "link": "https://friend.example"}]
        out = newsletter.build_html(NEWS[0], NEWS[3:4], ["Label:"], "Intro", cry, friends, {},
                                    themed=themed, world=world)
        i_cry = out.index("Today’s Rallying Cry")
        i_theme = out.index("Stories of Love")
        i_world = out.index("Around The World")
        i_friends = out.index("Friends of The Bright Spots")
        self.assertTrue(i_cry < i_theme < i_world < i_friends)
        self.assertIn("Love story of high school sweethearts.", out)
        self.assertIn(newsletter.reader_url("entertainment", NEWS[1]["title"]), out)

    def test_sections_left_out_when_empty(self):
        out = newsletter.build_html(NEWS[0], [], [], "Intro", None, [], {})
        self.assertNotIn(">Around The World<", out)


if __name__ == "__main__":
    unittest.main()
