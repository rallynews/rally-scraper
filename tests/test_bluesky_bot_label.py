import os
import unittest
from unittest import mock

import bluesky_bot_label
from bluesky_bot_label import pds_of, with_bot_label


class WithBotLabel(unittest.TestCase):
    def test_adds_the_label_and_keeps_everything_else(self):
        avatar = {'$type': 'blob', 'ref': {'$link': 'bafkrei'}, 'mimeType': 'image/jpeg', 'size': 1234}
        record = {'$type': 'app.bsky.actor.profile', 'displayName': 'Joyscroll finder', 'description': 'I am a bot', 'avatar': avatar}
        out = with_bot_label(record)
        self.assertEqual(out['labels'], {'$type': 'com.atproto.label.defs#selfLabels', 'values': [{'val': 'bot'}]})
        self.assertEqual(out['avatar'], avatar)
        self.assertEqual(out['description'], 'I am a bot')
        self.assertNotIn('labels', record)  # the original is untouched

    def test_keeps_other_self_labels(self):
        record = {'labels': {'$type': 'com.atproto.label.defs#selfLabels', 'values': [{'val': '!no-unauthenticated'}]}}
        self.assertEqual([v['val'] for v in with_bot_label(record)['labels']['values']], ['!no-unauthenticated', 'bot'])

    def test_does_nothing_when_already_labelled(self):
        record = {'labels': {'$type': 'com.atproto.label.defs#selfLabels', 'values': [{'val': 'bot'}]}}
        self.assertIsNone(with_bot_label(record))

    def test_makes_a_profile_when_there_is_none(self):
        self.assertEqual(with_bot_label({})['$type'], 'app.bsky.actor.profile')


class PdsOf(unittest.TestCase):
    def test_reads_the_pds_from_the_did_document(self):
        session = {'didDoc': {'service': [{'id': '#atproto_pds', 'type': 'AtprotoPersonalDataServer',
                                           'serviceEndpoint': 'https://shiitake.us-east.host.bsky.network/'}]}}
        self.assertEqual(pds_of(session), 'https://shiitake.us-east.host.bsky.network')

    def test_falls_back_to_the_entryway(self):
        self.assertEqual(pds_of({}), 'https://bsky.social')


class Run(unittest.TestCase):
    def _response(self, status, body):
        r = mock.Mock(status_code=status, text=str(body))
        r.json.return_value = body
        return r

    @mock.patch.dict(os.environ, {'BLUESKY_HANDLE': 'joyscroll-finder.rally.news', 'BLUESKY_APP_PASSWORD': 'x'})
    def test_signs_in_once_and_writes_the_profile_back_conditionally(self):
        session = {'did': 'did:plc:shp6', 'accessJwt': 'jwt',
                   'didDoc': {'service': [{'id': '#atproto_pds', 'serviceEndpoint': 'https://pds.example'}]}}
        profile = {'cid': 'bafyprofile', 'value': {'$type': 'app.bsky.actor.profile', 'displayName': 'Joyscroll finder'}}
        with mock.patch.object(bluesky_bot_label.requests, 'post') as post, \
             mock.patch.object(bluesky_bot_label.requests, 'get') as get:
            post.side_effect = [self._response(200, session), self._response(200, {'uri': 'at://x', 'cid': 'new'})]
            get.return_value = self._response(200, profile)
            self.assertEqual(bluesky_bot_label.main([]), 0)
        self.assertTrue(post.call_args_list[0].args[0].endswith('createSession'))
        url, kwargs = post.call_args_list[1].args[0], post.call_args_list[1].kwargs
        self.assertEqual(url, 'https://pds.example/xrpc/com.atproto.repo.putRecord')
        self.assertEqual(kwargs['json']['swapRecord'], 'bafyprofile')
        self.assertEqual(kwargs['json']['record']['displayName'], 'Joyscroll finder')
        self.assertEqual(kwargs['json']['record']['labels']['values'], [{'val': 'bot'}])
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer jwt')

    @mock.patch.dict(os.environ, {'BLUESKY_HANDLE': 'h', 'BLUESKY_APP_PASSWORD': 'x'})
    def test_dry_run_writes_nothing(self):
        with mock.patch.object(bluesky_bot_label.requests, 'post') as post, \
             mock.patch.object(bluesky_bot_label.requests, 'get') as get:
            post.return_value = self._response(200, {'did': 'did:plc:a', 'accessJwt': 'jwt'})
            get.return_value = self._response(200, {'cid': 'c', 'value': {}})
            self.assertEqual(bluesky_bot_label.main(['--dry-run']), 0)
        self.assertEqual(post.call_count, 1)


if __name__ == '__main__':
    unittest.main()
