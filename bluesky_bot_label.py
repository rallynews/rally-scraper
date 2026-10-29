"""Mark the Joyskies finder account as a bot on Bluesky. Run once.

    python bluesky_bot_label.py             # add the "bot" self-label to the profile
    python bluesky_bot_label.py --dry-run   # only say what the profile has now

Bluesky's guide for bots asks them to say so with a self-label on their
profile; its app has no switch for it, so this does it through the API
(.github/workflows/bluesky-bot-label.yml, run by hand from the Actions tab).

The profile is read as raw JSON and written back with only the label added,
so the name, description, picture and banner stay exactly as they are. The
write is conditional on the profile not having changed since it was read
(swapRecord). Running it again changes nothing.

Signs in with the same secrets as bluesky_clips.py (BLUESKY_HANDLE,
BLUESKY_APP_PASSWORD) — one sign-in per run.
"""

import os
import sys

import requests

ENTRYWAY = 'https://bsky.social'
PROFILE = 'app.bsky.actor.profile'
SELF_LABELS = 'com.atproto.label.defs#selfLabels'
CONTACT = os.environ.get('CLIPS_CONTACT_EMAIL') or 'joyscroll@rally.news'
HEADERS = {'User-Agent': f'RallyJoyscrollClips/1.0 (+https://rally.news/joyscroll/creators; {CONTACT})'}


def with_bot_label(record):
    """The profile record with the "bot" self-label added; None if it already has it. Pure."""
    record = dict(record or {})
    record['$type'] = PROFILE
    labels = record.get('labels')
    if isinstance(labels, dict) and labels.get('$type') == SELF_LABELS:
        values = [v for v in labels.get('values') or [] if isinstance(v, dict)]
    else:
        values = []
    if any(v.get('val') == 'bot' for v in values):
        return None
    record['labels'] = {'$type': SELF_LABELS, 'values': values + [{'val': 'bot'}]}
    return record


def pds_of(session):
    """The account's own server, from the session's DID document; the entryway if it has none."""
    for service in (session.get('didDoc') or {}).get('service') or []:
        if service.get('id', '').endswith('#atproto_pds') and service.get('serviceEndpoint'):
            return service['serviceEndpoint'].rstrip('/')
    return ENTRYWAY


def main(argv):
    dry_run = '--dry-run' in argv
    handle = os.environ.get('BLUESKY_HANDLE')
    password = os.environ.get('BLUESKY_APP_PASSWORD')
    if not handle or not password:
        print('BLUESKY_HANDLE and BLUESKY_APP_PASSWORD are needed.')
        return 1

    res = requests.post(f'{ENTRYWAY}/xrpc/com.atproto.server.createSession',
                        json={'identifier': handle, 'password': password}, headers=HEADERS, timeout=30)
    if res.status_code != 200:
        print(f'Sign-in failed ({res.status_code}). Check the two secrets.')
        return 1
    session = res.json()
    did, pds = session['did'], pds_of(session)
    auth = {**HEADERS, 'Authorization': f"Bearer {session['accessJwt']}"}

    res = requests.get(f'{pds}/xrpc/com.atproto.repo.getRecord',
                       params={'repo': did, 'collection': PROFILE, 'rkey': 'self'}, headers=HEADERS, timeout=30)
    if res.status_code == 200:
        current, cid = res.json().get('value') or {}, res.json().get('cid')
    elif res.status_code == 400 and 'RecordNotFound' in res.text:
        current, cid = {}, None
    else:
        print(f'Could not read the profile ({res.status_code}).')
        return 1

    labels = [v.get('val') for v in ((current.get('labels') or {}).get('values') or []) if isinstance(v, dict)]
    print(f'{handle} ({did}) — self-labels now: {", ".join(labels) or "none"}')
    updated = with_bot_label(current)
    if updated is None:
        print('Already marked as a bot. Nothing to do.')
        return 0
    if dry_run:
        print('Dry run: would add the "bot" label.')
        return 0

    body = {'repo': did, 'collection': PROFILE, 'rkey': 'self', 'record': updated}
    if cid:
        body['swapRecord'] = cid
    res = requests.post(f'{pds}/xrpc/com.atproto.repo.putRecord', json=body, headers=auth, timeout=30)
    if res.status_code != 200:
        print(f'Could not save the profile ({res.status_code}): {res.text[:300]}')
        return 1
    print('Done: the profile now carries the "bot" label.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
