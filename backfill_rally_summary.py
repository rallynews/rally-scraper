#!/usr/bin/env python3
"""Write Rally's own summary onto articles scraped before the scraper made one.

Every article from here on gets a `rally_summary` at scrape time. The rows
already in the table do not have one, and there are thousands of them — so this
is deliberately a manual, gated tool rather than a workflow. Running it over the
whole archive blind would be a few thousand model calls decided by nobody.

Start small and look at what comes back:

    python3 backfill_rally_summary.py --limit 20 --dry-run
    python3 backfill_rally_summary.py --limit 20
    python3 backfill_rally_summary.py --since 2026-06-01 --limit 500

Needs NEWS_API_URL, NEWS_API_KEY and OPENROUTER_API_KEY in the environment, the
same three the scraper itself runs on.
"""

import argparse
import sys
import time

from scraper import api_get, api_patch, generate_rally_summary

PAGE_SIZE = 500
# Small enough that a failure loses little, big enough to not be chatty.
PATCH_BATCH = 25


def fetch_all(since=None):
    """Every article the public API will hand over, newest first."""
    articles = []
    offset = 0
    while True:
        batch = api_get({'limit': PAGE_SIZE, 'offset': offset})
        if not batch:
            break
        articles.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    if since:
        articles = [a for a in articles if (a.get('timestamp') or '')[:10] >= since]
    return articles


def needs_summary(article):
    return not (article.get('rally_summary') or '').strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=25,
                        help='how many articles to write (default 25)')
    parser.add_argument('--since', metavar='YYYY-MM-DD',
                        help='only articles published on or after this date')
    parser.add_argument('--dry-run', action='store_true',
                        help='generate and print, write nothing')
    args = parser.parse_args()

    print('Fetching articles…')
    articles = fetch_all(args.since)
    if not articles:
        print('No articles came back from the API. Check NEWS_API_URL.')
        return 1

    pending = [a for a in articles if needs_summary(a)]
    print(f'{len(articles)} articles, {len(pending)} without a Rally summary.')
    if not pending:
        return 0

    targets = pending[:args.limit]
    print(f'Writing {len(targets)} of them.\n')

    updates = []
    written = 0
    skipped = 0

    for i, article in enumerate(targets, 1):
        title = article.get('title') or ''
        print(f'[{i}/{len(targets)}] {title[:70]}')

        text = generate_rally_summary(
            title,
            article.get('summary') or '',
            article.get('content') or '',
            article.get('source') or '',
        )
        if not text:
            skipped += 1
            continue

        print(f'    → {text}\n')
        if args.dry_run:
            written += 1
            continue

        updates.append({'url': article.get('url'), 'rally_summary': text})
        if len(updates) >= PATCH_BATCH:
            written += api_patch(updates)
            updates = []

        # The scraper paces itself the same way; no reason to be ruder here.
        time.sleep(1)

    if updates and not args.dry_run:
        written += api_patch(updates)

    verb = 'would write' if args.dry_run else 'wrote'
    print(f'\nDone — {verb} {written}, skipped {skipped} (model unavailable or output rejected).')
    if pending[args.limit:]:
        print(f'{len(pending) - len(targets)} still without one. Re-run to continue.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
