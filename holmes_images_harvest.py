#!/usr/bin/env python3
"""
Harvest Victorian London photo metadata from the Library of Congress.
Searches photos dated 1870-1910 with query "london", saves to holmes_images.json.

Each entry:
  {
    "id":       "loc.gov item ID",
    "title":    "...",
    "date":     "1895",
    "subjects": ["bridges", "rivers", "cityscapes", ...],
    "tags":     ["bridge", "river", "street", ...],   # normalised for matching
    "image_url": "https://tile.loc.gov/...v.jpg",     # ~1024px wide
    "thumb_url": "https://tile.loc.gov/..._150px.jpg"
  }

Usage:
    python3 holmes_images_harvest.py            # full harvest (resumable)
    python3 holmes_images_harvest.py --stats    # print tag frequency, no save
"""

import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

OUTPUT = Path(__file__).parent / 'holmes_images.json'

BASE_URL = 'https://www.loc.gov/photos/'
QUERY_PARAMS = 'q=london&dates=1870%2F1910&fo=json&c=25'
DELAY = 2.0   # seconds between requests — LOC rate-limits aggressively
RETRIES = 3

# Keyword → tag: matched against subjects AND title (substring match, lowercased)
TAG_KEYWORDS = [
    # Water / Thames
    ('river',       'river'),
    ('thames',      'river'),
    ('bridge',      'bridge'),
    ('dock',        'dock'),
    ('harbour',     'dock'),
    ('harbor',      'dock'),
    ('wharf',       'dock'),
    ('embankment',  'river'),
    ('boat',        'boat'),
    ('ship',        'boat'),
    ('vessel',      'boat'),
    # Streets
    ('street',      'street'),
    ('road',        'street'),
    ('lane',        'street'),
    ('alley',       'street'),
    ('cityscape',   'cityscape'),
    ('market',      'market'),
    ('shop',        'shop'),
    ('crowd',       'crowd'),
    ('omnibus',     'street'),
    ('hansom',      'street'),
    # Architecture / landmarks
    ('church',      'church'),
    ('cathedral',   'church'),
    ('abbey',       'church'),
    ('palace',      'palace'),
    ('parliament',  'parliament'),
    ('tower',       'tower'),
    ('castle',      'tower'),
    ('monument',    'monument'),
    ('station',     'station'),
    # Parks / nature
    ('park',        'park'),
    ('garden',      'garden'),
    ('square',      'park'),
    # People
    ('portrait',    'portrait'),
    ('worker',      'crowd'),
    ('people',      'crowd'),
    # Atmosphere
    ('fog',         'fog'),
    ('night',       'night'),
    ('rain',        'rain'),
    ('smoke',       'fog'),
    # Interiors
    ('interior',    'interior'),
    ('room',        'interior'),
    ('hall',        'interior'),
    ('library',     'interior'),
]


def fetch_page(page):
    url = f'{BASE_URL}?{QUERY_PARAMS}&sp={page}'
    for attempt in range(RETRIES):
        result = subprocess.run(
            ['curl', '-s', '--max-time', '45',
             '-H', 'User-Agent: Holmes-Quote-Bot/1.0 (personal project)',
             url],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            if attempt < RETRIES - 1:
                time.sleep(5)
                continue
            raise RuntimeError(f'curl failed: {result.stderr}')
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            if attempt < RETRIES - 1:
                time.sleep(5)
                continue
            raise RuntimeError('Truncated/invalid JSON response')
    raise RuntimeError('All retries exhausted')


def best_image_url(image_urls):
    """Pick the largest available image (v.jpg > r.jpg > fallback). Rejects SVGs."""
    if not image_urls:
        return None
    cleaned = [u.split('#')[0] for u in image_urls]
    for suffix in ('v.jpg', 'v.tif', 'r.jpg'):
        for u in cleaned:
            if u.endswith(suffix):
                return u
    # Fall back to first non-SVG URL
    for u in cleaned:
        if not u.endswith('.svg'):
            return u
    return None


def thumb_url(image_urls):
    if not image_urls:
        return None
    for u in image_urls:
        u = u.split('#')[0]
        if '_150px' in u or '_th.jpg' in u or 't.gif' in u:
            return u
    return image_urls[0].split('#')[0]


def normalise_tags(subjects, title=''):
    tags = set()
    text = ' '.join(subjects or []) + ' ' + (title or '')
    text = text.lower()
    for keyword, tag in TAG_KEYWORDS:
        if keyword in text:
            tags.add(tag)
    return sorted(tags)


def parse_result(r):
    item_id = r.get('id', '')
    title = r.get('title', '').strip('[]').strip()
    date = r.get('date', '')[:4] if r.get('date') else ''
    subjects = r.get('subject') or []
    image_urls = r.get('image_url') or []

    img = best_image_url(image_urls)
    if not img:
        return None

    return {
        'id': item_id,
        'title': title,
        'date': date,
        'subjects': subjects,
        'tags': normalise_tags(subjects, title),
        'image_url': img,
        'thumb_url': thumb_url(image_urls),
    }


def main():
    stats_only = '--stats' in sys.argv

    # Load existing output for resumption
    existing = {}
    if OUTPUT.exists() and not stats_only:
        try:
            for entry in json.loads(OUTPUT.read_text()):
                existing[entry['id']] = entry
            print(f'Resuming: {len(existing)} entries already saved')
        except Exception:
            pass

    # Get total page count
    print('Fetching page 1 to determine total...', flush=True)
    data = fetch_page(1)
    p = data.get('pagination', {})
    total_pages = p.get('total', 1)
    total_results = p.get('of', '?')
    print(f'Total results: {total_results}, pages: {total_pages}')

    all_entries = list(existing.values())
    seen_ids = set(existing.keys())

    # Process page 1 results
    for r in data.get('content', {}).get('results', []):
        entry = parse_result(r)
        if entry and entry['id'] not in seen_ids:
            seen_ids.add(entry['id'])
            all_entries.append(entry)

    # Fetch remaining pages
    for page in range(2, total_pages + 1):
        print(f'\rPage {page}/{total_pages} ({len(all_entries)} entries)...', end='', flush=True)
        time.sleep(DELAY)
        try:
            data = fetch_page(page)
        except Exception as e:
            print(f'\nFailed on page {page}: {e}')
            # Save progress before exiting
            if not stats_only:
                OUTPUT.write_text(json.dumps(all_entries, indent=2, ensure_ascii=False))
                print(f'Progress saved ({len(all_entries)} entries). Re-run to resume.')
            break
        for r in data.get('content', {}).get('results', []):
            entry = parse_result(r)
            if entry and entry['id'] not in seen_ids:
                seen_ids.add(entry['id'])
                all_entries.append(entry)
        # Save every 20 pages
        if not stats_only and page % 20 == 0:
            OUTPUT.write_text(json.dumps(all_entries, indent=2, ensure_ascii=False))

    print(f'\n\nTotal entries: {len(all_entries)}')

    if stats_only:
        tag_counts = Counter(tag for e in all_entries for tag in e['tags'])
        print('\nTag frequency:')
        for tag, count in tag_counts.most_common():
            print(f'  {count:4d}  {tag}')
        untagged = sum(1 for e in all_entries if not e['tags'])
        print(f'\nUntagged: {untagged} ({100*untagged//len(all_entries)}%)')
        return

    OUTPUT.write_text(json.dumps(all_entries, indent=2, ensure_ascii=False))
    print(f'Saved to {OUTPUT}')


if __name__ == '__main__':
    main()
