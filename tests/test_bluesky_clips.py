"""The Joy clips finding job, with Bluesky, Rally's API and the models faked.
Run with:  python -m unittest discover tests
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bluesky_clips as bc  # noqa: E402

DID = 'did:plc:maya'


def post(rkey, did=DID, cid=None, text='Rescued him six weeks ago and today he saw the sea 🌊', langs=('en',),
         labels=(), author_labels=(), video=True, ratio=(9, 16), reply=False, embed=None):
    e = embed or ({'$type': bc.VIDEO_VIEW, 'cid': 'v', 'playlist': 'https://video/p.m3u8',
                   'thumbnail': f'https://video/{rkey}.jpg', 'alt': 'A dog runs into the sea',
                   'aspectRatio': {'width': ratio[0], 'height': ratio[1]}} if video else
                  {'$type': 'app.bsky.embed.images#view'})
    record = {'$type': 'app.bsky.feed.post', 'text': text, 'langs': list(langs)}
    if reply:
        record['reply'] = {'root': {}, 'parent': {}}
    return {'uri': f'at://{did}/app.bsky.feed.post/{rkey}', 'cid': cid or f'bafy{rkey}', 'record': record,
            'author': {'did': did, 'handle': 'maya.bsky.social', 'displayName': 'Maya',
                       'labels': [{'val': v} for v in author_labels]},
            'labels': [{'val': v} for v in labels], 'embed': e}


class FilterTests(unittest.TestCase):
    def check(self, p, known=(), blocked=()):
        return bc.hard_filter(p, set(known), set(blocked))

    def test_keeps_an_english_vertical_original_video(self):
        self.assertEqual(self.check(post('a')), (None, False))

    def test_quiet_skips_are_not_stored(self):
        self.assertEqual(self.check(post('a', video=False)), ('not a video', False))
        self.assertEqual(self.check(post('a'), known=['at://did:plc:maya/app.bsky.feed.post/a']), ('already seen', False))
        self.assertEqual(self.check(post('a'), blocked=[DID])[1], False)

    def test_turns_down_replies_quotes_labels_language_and_landscape(self):
        self.assertEqual(self.check(post('a', reply=True)), ('a reply', True))
        self.assertEqual(self.check(post('a', labels=['porn'])), ('label: porn', True))
        self.assertEqual(self.check(post('a', labels=['!no-unauthenticated'])), ('label: !no-unauthenticated', True))
        self.assertEqual(self.check(post('a', langs=['de'])), ('not English', True))
        self.assertEqual(self.check(post('a', langs=['en-GB'])), (None, False))
        self.assertEqual(self.check(post('a', ratio=(16, 9))), ('landscape', True))

    def test_a_quote_with_video_passes_only_when_it_quotes_the_same_creator(self):
        video = post('v')['embed']
        own = {'$type': bc.RECORD_WITH_MEDIA_VIEW, 'media': video, 'record': {'record': {'author': {'did': DID}}}}
        other = {'$type': bc.RECORD_WITH_MEDIA_VIEW, 'media': video, 'record': {'record': {'author': {'did': 'did:plc:x'}}}}
        self.assertEqual(self.check(post('a', embed=own)), (None, False))
        self.assertEqual(self.check(post('a', embed=other)), ('quotes someone else', True))

    def test_any_label_on_the_creator_keeps_them_out(self):
        self.assertEqual(bc.excluded_label({'labels': []}, {'labels': [{'val': '!no-unauthenticated'}]}),
                         'creator hides from logged-out viewers')
        self.assertEqual(bc.excluded_label({'labels': []}, {'labels': [{'val': 'bot'}]}), 'creator label: bot')
        self.assertIsNone(bc.excluded_label({'labels': []}, {'labels': []}))

    def test_text_verdict(self):
        ok = {'joy_score': 9, 'category': 'animals', 'topics': ['Dog', 'sea'], 'notes': 'A dog meets the sea.',
              **{f: False for f in bc.TEXT_FLAGS}}
        fields, reason = bc.judge_text(ok)
        self.assertIsNone(reason)
        self.assertEqual(fields['topics'], ['dog', 'sea'])
        self.assertEqual(bc.judge_text({**ok, 'joy_score': 5})[1], 'text: joy below 7')
        self.assertEqual(bc.judge_text({**ok, 'fundraising': True})[1], 'text: fundraising')
        self.assertEqual(bc.judge_text({**ok, 'category': 'politics'})[1], 'text: no category')
        self.assertEqual(bc.judge_text('nonsense'), (None, None))

    def test_thumbnail_verdict(self):
        clean = {f: False for f in bc.THUMB_FLAGS}
        self.assertIsNone(bc.judge_thumbnail(clean))
        self.assertEqual(bc.judge_thumbnail({**clean, 'tv_or_film_footage': True}), 'thumbnail: tv_or_film_footage')
        self.assertEqual(bc.judge_thumbnail({'watermark_other_platform': False}), '')

    def test_recheck_verdict(self):
        clip = {'cid': 'bafya'}
        self.assertEqual(bc.recheck_verdict(clip, None), 'gone')
        self.assertEqual(bc.recheck_verdict(clip, post('a', cid='bafyb')), 'changed')
        self.assertEqual(bc.recheck_verdict(clip, post('a', cid='bafya', labels=['rude'])), 'relabelled')
        self.assertEqual(bc.recheck_verdict(clip, post('a', cid='bafya'), {'labels': [{'val': 'spam'}]}), 'relabelled')
        self.assertEqual(bc.recheck_verdict(clip, post('a', cid='bafya'), {'labels': []}), 'ok')


class FakeBluesky:
    def __init__(self, pages, profiles=None, posts=None, stop_after=None):
        self.pages, self.profile_map, self.post_map = pages, profiles or {}, posts or {}
        self.calls, self.logins, self.stop_after, self.searched = 0, 0, stop_after, []

    def login(self):
        self.logins += 1

    def search(self, q, since, cursor=None):
        self.calls += 1
        if self.stop_after is not None and self.calls > self.stop_after:
            raise bc.StopRun('three 429s in a row')
        self.searched.append((q, since, cursor))
        return self.pages.get((q, cursor), ([], None))

    def profiles(self, dids):
        return {d: self.profile_map.get(d, {'did': d, 'labels': []}) for d in dids}

    def posts(self, uris):
        return {u: self.post_map[u] for u in uris if u in self.post_map}


class RunTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._api, self._model = bc.api, bc.call_model

        def fake_api(method, params=None, payload=None, dry_run=False):
            if method == 'GET' and params['action'] == 'known':
                return {'uris': ['at://did:plc:maya/app.bsky.feed.post/seen'], 'dids': ['did:plc:blocked'],
                        'queries': [{'id': 1, 'query': 'rescue dog'}, {'id': 2, 'query': '#birds'}]}
            if method == 'GET' and params['action'] == 'recheck':
                return {'clips': [{'id': 1, 'at_uri': 'at://did:plc:maya/app.bsky.feed.post/a', 'cid': 'bafya', 'author_did': DID},
                                  {'id': 2, 'at_uri': 'at://did:plc:maya/app.bsky.feed.post/b', 'cid': 'bafyb', 'author_did': DID},
                                  {'id': 3, 'at_uri': 'at://did:plc:maya/app.bsky.feed.post/c', 'cid': 'bafyc', 'author_did': DID}]}
            self.sent.append(payload)
            return {'success': True}

        def fake_model(model, content, budget, max_tokens=400):
            budget.calls += 1
            if isinstance(content, list):  # the thumbnail check
                url = content[1]['image_url']['url']
                return {f: (f == 'watermark_other_platform' and 'tiktok' in url) for f in bc.THUMB_FLAGS}
            sad = 'lost' in content
            return {'joy_score': 3 if sad else 9, 'category': 'animals', 'topics': ['dog'], 'notes': 'A dog meets the sea.',
                    **{f: False for f in bc.TEXT_FLAGS}}

        bc.api, bc.call_model = fake_api, fake_model

    def tearDown(self):
        bc.api, bc.call_model = self._api, self._model

    def test_a_find_run_posts_identifiers_and_verdicts_only(self):
        pages = {
            ('rescue dog', None): ([post('good'), post('seen'), post('pic', video=False), post('sad', text='We lost him today'),
                                    post('rep', reply=True), post('mark', did='did:plc:mark')], 'c2'),
            ('rescue dog', 'c2'): ([post('tiktok', did='did:plc:tt')], None),
            ('#birds', None): ([post('robin', did='did:plc:bird'), post('blk', did='did:plc:blocked')], None),
        }
        fake = FakeBluesky(pages, profiles={'did:plc:mark': {'did': 'did:plc:mark', 'labels': [{'val': '!no-unauthenticated'}]}})
        stats = bc.find(bsky=fake, now=datetime(2026, 10, 26, 12, tzinfo=timezone.utc))
        self.assertEqual(fake.logins, 1)
        self.assertEqual(fake.searched[0], ('rescue dog', '2026-10-24T12:00:00Z', None))
        payload = self.sent[0]
        by = {i['rkey']: i for i in payload['items']}
        self.assertEqual(by['good']['status'], 'pending')
        self.assertEqual(by['robin']['status'], 'pending')
        self.assertEqual(by['sad']['reject_reason'], 'text: joy below 7')
        self.assertEqual(by['rep']['reject_reason'], 'a reply')
        self.assertEqual(by['mark']['reject_reason'], 'creator hides from logged-out viewers')
        self.assertEqual(by['tiktok']['reject_reason'], 'thumbnail: watermark_other_platform')
        for quiet in ('seen', 'pic', 'blk'):
            self.assertNotIn(quiet, by)
        self.assertEqual(stats['candidates'], 2)
        self.assertEqual(payload['queries'], [{'id': 1, 'found': 5}, {'id': 2, 'found': 1}])
        # Nothing of the post itself leaves the job: no text, handle, name or media URL.
        sent = json.dumps(payload)
        for leak in ('Rescued him', 'lost him', 'maya.bsky.social', 'Maya"', 'video/', 'playlist', 'A dog runs'):
            self.assertNotIn(leak, sent)
        allowed = {'at_uri', 'cid', 'author_did', 'rkey', 'status', 'reject_reason', 'found_by_query',
                   'joy_score', 'category', 'topics', 'check_notes'}
        for item in payload['items']:
            self.assertLessEqual(set(item), allowed)

    def test_a_stop_from_bluesky_ends_the_run_cleanly_and_asks_nothing_more(self):
        # A reply is turned down by code alone, so it is kept; the video that
        # still needed its creator checked is not — the next run finds it again.
        pages = {('rescue dog', None): ([post('good'), post('rep', reply=True)], 'c2')}
        fake = FakeBluesky(pages, stop_after=1)
        fake.profiles = lambda dids: self.fail('asked Bluesky for profiles after being told to stop')
        stats = bc.find(bsky=fake)
        self.assertEqual(stats['searches'], 1)
        self.assertEqual([(i['rkey'], i['status']) for i in self.sent[0]['items']], [('rep', 'rejected')])

    def test_the_model_cap_holds(self):
        many = [post(f'p{i}', did=f'did:plc:c{i}') for i in range(50)]
        pages = {('rescue dog', None): (many, None)}
        stats = bc.find(bsky=FakeBluesky(pages))
        self.assertLessEqual(stats['candidates'], bc.MAX_CANDIDATES)
        self.assertEqual(stats['candidates'], bc.MAX_MODEL_CALLS // 2)

    def test_recheck_reports_gone_changed_and_relabelled(self):
        fake = FakeBluesky({}, posts={
            'at://did:plc:maya/app.bsky.feed.post/a': post('a', cid='bafya'),
            'at://did:plc:maya/app.bsky.feed.post/b': post('b', cid='bafyNEW'),
        })
        counts = bc.recheck(bsky=fake)
        results = {r['id']: r for r in self.sent[0]['results']}
        self.assertEqual(results[1]['result'], 'ok')
        self.assertEqual(results[2], {'id': 2, 'result': 'changed', 'cid': 'bafyNEW'})
        self.assertEqual(results[3]['result'], 'gone')
        self.assertEqual(counts, {'ok': 1, 'gone': 1, 'changed': 1, 'relabelled': 0})


class ClientTests(unittest.TestCase):
    """The wrapper around the atproto SDK: our headers, spacing and backoff."""

    def setUp(self):
        try:
            import atproto  # noqa: F401
        except ImportError:
            self.skipTest('atproto not installed')
        self.slept = []
        self._sleep = bc.time.sleep
        bc.time.sleep = lambda s: self.slept.append(s)

    def tearDown(self):
        bc.time.sleep = self._sleep

    def test_sends_our_user_agent_and_bluesky_labels_header(self):
        headers = {k.lower(): v for k, v in bc.Bluesky().client.request.get_headers().items()}
        self.assertTrue(headers['user-agent'].startswith('RallyJoyscrollClips/1.0 (+https://rally.news/joyscroll/creators;'))
        self.assertEqual(headers['atproto-accept-labelers'], bc.BSKY_LABELER_DID)

    def test_backs_off_on_429_and_stops_after_three(self):
        from atproto_client import exceptions as ex
        b = bc.Bluesky()
        attempts = []

        def limited():
            attempts.append(1)
            raise ex.RateLimitExceededError(None)
        with self.assertRaises(bc.StopRun):
            b._call(limited)
        self.assertEqual(len(attempts), 3)
        self.assertTrue(any(s >= 2 for s in self.slept))

    def test_a_refusal_stops_at_once(self):
        from atproto_client import exceptions as ex
        b = bc.Bluesky()

        def refused():
            raise ex.UnauthorizedError(None)
        with self.assertRaises(bc.StopRun):
            b._call(refused)
        self.assertEqual(b.calls, 1)

    def test_recovers_after_one_429(self):
        from atproto_client import exceptions as ex
        b = bc.Bluesky()
        tries = iter([ex.RateLimitExceededError(None), 'ok'])

        def flaky():
            t = next(tries)
            if isinstance(t, Exception):
                raise t
            return t
        self.assertEqual(b._call(flaky), 'ok')


if __name__ == '__main__':
    unittest.main()
