#!/usr/bin/env python3
"""
Post one Sherlock Holmes quote with a matched Victorian London image to Bluesky.
Picks an unposted quote from holmes_quotes.json, finds a matching image from
holmes_images.json, and posts both.

State is tracked in holmes_state.json (posted quote IDs).

Requires:
    security add-generic-password -a "<handle>" -s "holmesbot-bluesky" -w

Usage:
    python3 holmes_post.py           # post one item
    python3 holmes_post.py --dry-run # print post without posting
"""

import hashlib
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from atproto import Client, client_utils, models

import net_guard

QUOTES_FILE  = Path(__file__).parent / 'holmes_quotes.json'
IMAGES_FILE  = Path(__file__).parent / 'holmes_scenes.json'
STATE_FILE   = Path(__file__).parent / 'holmes_state.json'

HANDLE           = 'sherlockquotes.bsky.social'
KEYCHAIN_SERVICE = 'holmesbot-bluesky'
DRY_RUN          = '--dry-run' in sys.argv

MAX_CHARS = 290  # 10-char buffer under Bluesky's 300 limit

BOOK_META = {
    'A Study in Scarlet':                ('https://www.gutenberg.org/ebooks/244',   '🩸'),
    'The Sign of the Four':              ('https://www.gutenberg.org/ebooks/2097',  '4️⃣'),
    'The Adventures of Sherlock Holmes': ('https://www.gutenberg.org/ebooks/1661',  '🎩'),
    'The Memoirs of Sherlock Holmes':    ('https://www.gutenberg.org/ebooks/834',   '📖'),
    'The Return of Sherlock Holmes':     ('https://www.gutenberg.org/ebooks/108',   '↩️'),
    'The Hound of the Baskervilles':     ('https://www.gutenberg.org/ebooks/2852',  '🐕'),
    'The Valley of Fear':                ('https://www.gutenberg.org/ebooks/3289',  '😨'),
    'His Last Bow':                      ('https://www.gutenberg.org/ebooks/2350',  '🎻'),
    'The Case-Book of Sherlock Holmes':  ('https://www.gutenberg.org/ebooks/69700', '🗂️'),
}

# Hashtags appended (as clickable facets) to the attribution post. Each entry is
# (display_text, tag_value); the '#' is added at render time.
TAGS = [
    ('SherlockHolmes', 'SherlockHolmes'),
]


def _story_key(s):
    """Normalise a story title for emoji lookup (case/quote/period-insensitive)."""
    s = s.lower().replace('’', "'").replace('‘', "'")
    s = s.replace('“', '"').replace('”', '"')
    return re.sub(r'\s+', ' ', s.rstrip('.').strip())


# Per-story emoji, keyed by normalised story title (see _story_key). A collection
# quote uses its story's emoji; novels and any unmapped/story-less quote fall back
# to the collection emoji in BOOK_META.
STORY_EMOJI = {_story_key(k): v for k, v in {
    # The Adventures of Sherlock Holmes
    'A Scandal in Bohemia':                        '🖼️',
    'The Red-Headed League':                       '🦰',
    'A Case of Identity':                          '⌨️',
    'The Boscombe Valley Mystery':                 '🏞️',
    'The Five Orange Pips':                        '🍊',
    'The Man with the Twisted Lip':                '👄',
    'The Adventure of the Blue Carbuncle':         '🪿',
    'The Adventure of the Speckled Band':          '🐍',
    "The Adventure of the Engineer's Thumb":       '⚙️',
    'The Adventure of the Noble Bachelor':         '💒',
    'The Adventure of the Beryl Coronet':          '👑',
    'The Adventure of the Copper Beeches':         '🌳',
    # The Memoirs of Sherlock Holmes
    'Silver Blaze':                                '🐎',
    'The Adventure of the Cardboard Box':          '📦',
    'The Yellow Face':                             '🎭',
    "The Stockbroker's Clerk":                     '💼',
    'The "Gloria Scott"':                          '🚢',
    'The Musgrave Ritual':                         '📜',
    'The Reigate Squires':                         '📝',
    'The Crooked Man':                             '🪖',
    'The Resident Patient':                        '🩺',
    'The Greek Interpreter':                       '🗣️',
    'The Naval Treaty':                            '⚓',
    'The Final Problem':                           '🌊',
    # The Return of Sherlock Holmes
    'The Adventure of the Empty House':            '🏚️',
    'The Adventure of the Norwood Builder':        '🧱',
    'The Adventure of the Dancing Men':            '🕺',
    'The Adventure of the Solitary Cyclist':       '🚲',
    'The Adventure of the Priory School':          '🏫',
    'The Adventure of Black Peter':                '🔱',
    'The Adventure of Charles Augustus Milverton': '✉️',
    'The Adventure of the Six Napoleons':          '🗿',
    'The Adventure of the Three Students':         '🎓',
    'The Adventure of the Golden Pince-Nez':       '👓',
    'The Adventure of the Missing Three-Quarter':  '🏉',
    'The Adventure of the Abbey Grange':           '🍷',
    'The Adventure of the Second Stain':           '🖋️',
    # His Last Bow
    'The Adventure of Wisteria Lodge':             '🌸',
    'The Adventure of the Bruce-Partington Plans': '🚇',
    "The Adventure of the Devil's Foot":           '😈',
    'The Adventure of the Red Circle':             '🔴',
    'The Disappearance of Lady Frances Carfax':    '⚰️',
    'The Adventure of the Dying Detective':        '🤒',
    'His Last Bow: The War Service of Sherlock Holmes': '🌬️',
    # The Case-Book of Sherlock Holmes
    'The Adventure of the Illustrious Client':     '📔',
    'The Adventure of the Blanched Soldier':       '🎖️',
    'The Adventure of the Mazarin Stone':          '💎',
    'The Adventure of the Three Gables':           '🏠',
    'The Adventure of the Sussex Vampire':         '🧛',
    'The Adventure of the Three Garridebs':        '💵',
    'The Problem of Thor Bridge':                  '🌉',
    'The Adventure of the Creeping Man':           '🐒',
    "The Adventure of the Lion's Mane":            '🪼',
    'The Adventure of the Veiled Lodger':          '🧕',
    'The Adventure of Shoscombe Old Place':        '🏇',
    'The Adventure of the Retired Colourman':      '🎨',
}.items()}

def keychain_password(account, service):
    result = subprocess.run(
        ['security', 'find-generic-password', '-a', account, '-s', service, '-w'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'No Keychain password for account="{account}" service="{service}".\n'
            f'Add it with:\n'
            f'  security add-generic-password -a "{account}" -s "{service}" -w'
        )
    return result.stdout.strip()


def quote_id(text):
    """Stable identity for a quote, derived from its text. The posted-state in
    holmes_state.json is keyed by this, so it survives re-harvesting or
    reordering of holmes_quotes.json (unlike the old array-index scheme)."""
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {'posted': []}


def save_state(state):
    # Sibling temp file + atomic rename: a crash mid-write can never leave a
    # truncated state file behind (which would break dedup and cause reposts).
    tmp = STATE_FILE.with_name(STATE_FILE.name + '.tmp')
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_FILE)


def is_complete_quote(text):
    """True if the quote is a complete, standalone sentence."""
    return (
        text.endswith(('.', '!', '?'))
        and len(text) >= 60
        and text[0].isupper()
    )


# Share of posts drawn from quotes whose own work has a Paget scene. Measured
# 22 Jul 2026: 506 of 2,230 postable quotes (23%) share a work with a scene —
# 299 of them from The Hound of the Baskervilles alone — and an unbiased draw
# lands on one ~21% of the time. The bias lifts truly-matched posts to ~47%,
# and the draw is flattened BY WORK first (one work, then one quote within
# it), because a uniform draw over matched quotes would be ~60% Hound and
# turn the feed into a Hound account.
MATCHED_ART_BIAS = 1 / 3


def _matched_story_keys(images):
    """Fuzzy keys of every story that has its own Strand Paget scene."""
    return {_match_key(s['story']) for s in images
            if s.get('source') == 'strand' and s.get('story')}


def pick_quote(quotes, posted_ids, images=None):
    unposted = [
        q for q in quotes
        if quote_id(q['quote']) not in posted_ids and is_complete_quote(q['quote'])
    ]
    if not unposted:
        return None
    # Matched-art bias: sometimes restrict the draw to quotes whose story (or,
    # for the novels, whose book) has its own scene, so pick_images finds a
    # true match. Falls through unbiased once the matched sliver is exhausted.
    if images and random.random() < MATCHED_ART_BIAS:
        keys = _matched_story_keys(images)
        by_work = {}
        for q in unposted:
            k = _match_key(q.get('story') or q.get('book'))
            if k in keys:
                by_work.setdefault(k, []).append(q)
        if by_work:
            unposted = by_work[random.choice(sorted(by_work))]
    # Dialogue is ~10% of the pool; post it 40% of the time to over-represent it for variety.
    dialogue = [q for q in unposted if q.get('speaker') != 'narrative']
    narrative = [q for q in unposted if q.get('speaker') == 'narrative']
    if dialogue and (not narrative or random.random() < 0.4):
        return random.choice(dialogue)
    return random.choice(narrative)


def _match_key(s):
    """Fuzzy key for comparing a quote's story to a scene's story, tolerant of
    'The Adventure of ...' prefixes and singular/plural differences.
    e.g. 'Silver Blaze' and 'The Adventure of Silver Blaze' -> 'silver blaze'."""
    s = (s or '').lower().replace('’', "'").replace('‘', "'")
    s = re.sub(r'[^a-z0-9 ]', '', s)
    s = re.sub(r'^(the adventure of the |the adventure of |the |a |an )', '', s)
    s = s.strip()
    return s[:-1] if s.endswith('s') else s


ATMOSPHERE_WORDS = (
    'fog', 'mist', 'moor', 'night', 'midnight', 'moon', 'dark', 'shadow',
    'gloom', 'ghost', 'spectre', 'specter', 'phantom', 'grave', 'death',
    'storm', 'candle', 'lamp', 'lantern',
)


def is_atmospheric(quote_text):
    """True if a quote is eerie enough to justify a ghoulish atmosphere image."""
    lower = quote_text.lower()
    return any(re.search(rf'\b{w}\b', lower) for w in ATMOSPHERE_WORDS)


def pick_images(images, quote_entry, n=6):
    """Ordered scene candidates for a quote: same story first, then same book,
    then any Paget scene. Occasionally leads with a British Library 'ghoulish'
    atmosphere scene when the quote is suitably eerie."""
    strand = [s for s in images if s.get('source') == 'strand']
    atmos  = [s for s in images if s.get('source') == 'british_library']
    qbook  = quote_entry.get('book')
    qstory = quote_entry.get('story')

    candidates = []

    # Occasional atmosphere lead-in for eerie quotes
    if atmos and is_atmospheric(quote_entry['quote']) and random.random() < 0.25:
        picks = atmos[:]
        random.shuffle(picks)
        candidates.extend(picks[:2])

    # Same story (fuzzy match)
    if qstory:
        sk = _match_key(qstory)
        matched = [s for s in strand if s.get('story') and _match_key(s['story']) == sk]
        random.shuffle(matched)
        candidates.extend(matched)

    # Same book
    if qbook:
        matched = [s for s in strand if s.get('book') == qbook and s not in candidates]
        random.shuffle(matched)
        candidates.extend(matched)

    # Any Paget scene, then any atmosphere scene as last resort
    rest = [s for s in strand if s not in candidates]
    random.shuffle(rest)
    candidates.extend(rest)
    rest2 = [s for s in atmos if s not in candidates]
    random.shuffle(rest2)
    candidates.extend(rest2)

    return candidates[:n]


SPEAKER_NAMES = {
    'Holmes':      'Sherlock Holmes',
    'Mycroft':     'Mycroft Holmes',
    'Lestrade':    'Inspector Lestrade',
    'Watson':      'Dr. Watson',
    'Moriarty':    'Professor Moriarty',
    'Moran':       'Colonel Moran',
    'Irene':       'Irene Adler',
    'Irene Adler': 'Irene Adler',
}


def format_quote(quote):
    """Return quote text with curly apostrophes, wrapped in curly double quotes."""
    quote = re.sub(r"(\w)'(\w)", lambda m: m.group(1) + '’' + m.group(2), quote)
    quote = quote.replace("'", '’')
    return '“' + quote + '”'


def build_post1(quote):
    """Post 1: just the quote."""
    tb = client_utils.TextBuilder()
    tb.text(format_quote(quote))
    return tb


def scene_source_note(book, story, image_entry):
    """A parenthetical naming the illustration's own story when it is not the
    quote's. Only ~23% of postable quotes share a work with any Paget scene
    (measured 22 Jul 2026: 506 of 2,230, of which 299 are Hound; 56% of quotes
    have no same-book scene at all), so most posts carry cross-story art; name
    it honestly rather than presenting it as the scene. A scene whose story is unknown but whose book differs is named by
    its book; a scene with no book/story metadata (atmosphere art) gets no
    note."""
    img_story = image_entry.get('story')
    img_book = image_entry.get('book')
    if not (img_story or img_book):
        return ''
    quote_key = _match_key(story) if story else _match_key(book)
    img_key = _match_key(img_story) if img_story else _match_key(img_book)
    if quote_key and img_key == quote_key:
        return ''
    if img_story:
        return f' (from “{img_story}”)'
    return f' (from {img_book})'


def append_attribution(tb, speaker, book, story, image_entry):
    """Append the attribution + photo credit to an existing TextBuilder."""
    book_url, book_emoji = BOOK_META.get(book, (None, '\U0001f4da'))
    # A collection story uses its own emoji; novels and unmapped/story-less
    # quotes fall back to the collection emoji.
    emoji = STORY_EMOJI.get(_story_key(story), book_emoji) if story else book_emoji
    credit_name = image_entry.get('credit_name', 'Wikimedia Commons')
    page_url = image_entry.get('page_url', '')

    if speaker and speaker != 'narrative':
        full_name = SPEAKER_NAMES.get(speaker, speaker)
        tb.text(f'— {full_name}, ')
    else:
        tb.text('— ')
    # Title: for a collection story, name the story in quotes and link it to the
    # collection; for the 4 novels (or an unlocated quote) link the book itself.
    if story:
        tb.text('“')
        tb.link(story, book_url) if book_url else tb.text(story)
        tb.text('”')
    elif book_url:
        tb.link(book, book_url)
    else:
        tb.text(book)
    tb.text(f' {emoji}\n\n✒️ ')
    if page_url:
        tb.link(credit_name, page_url)
    else:
        tb.text(credit_name)
    note = scene_source_note(book, story, image_entry)
    if note:
        tb.text(note)
    # Hashtags as clickable facets, on their own line under the credit.
    if TAGS:
        tb.text('\n\n')
        for i, (display, tag) in enumerate(TAGS):
            if i:
                tb.text(' ')
            tb.tag('#' + display, tag)
    return tb


def build_post2(speaker, book, story, image_entry):
    """Threaded reply: attribution + photo credit on their own post."""
    return append_attribution(client_utils.TextBuilder(), speaker, book, story, image_entry)


def build_combined(quote, speaker, book, story, image_entry):
    """Single post: quote, then attribution + photo credit."""
    tb = client_utils.TextBuilder()
    tb.text(format_quote(quote))
    tb.text('\n\n')
    append_attribution(tb, speaker, book, story, image_entry)
    return tb


MAX_IMAGE_BYTES = 950_000  # stay under Bluesky's ~1 MB blob limit


def fetch_image(url):
    """Fetch a Commons Special:FilePath image, stepping the requested width down
    until the payload fits under Bluesky's blob limit."""
    for width in (1000, 800, 640, 500):
        u = re.sub(r'width=\d+', f'width={width}', url) if 'width=' in url else url
        result = subprocess.run(
            ['curl', '-s', '-L', '--max-time', '40', '-o', '-', u],
            capture_output=True
        )
        if result.returncode == 0 and 1000 < len(result.stdout) <= MAX_IMAGE_BYTES:
            return result.stdout
        if 'width=' not in url:
            break
    raise RuntimeError(f'Failed or oversized image: {url}')


def main():
    quotes = json.loads(QUOTES_FILE.read_text())
    images = json.loads(IMAGES_FILE.read_text())
    state  = load_state()
    posted_ids = set(state.get('posted', []))

    # Pick quote
    quote_entry = pick_quote(quotes, posted_ids, images)
    if quote_entry is None:
        print('All quotes have been posted. Reset holmes_state.json to restart.')
        sys.exit(0)

    # Daily, so half an hour of waiting is free, and the rest of the run needs
    # the network throughout. Gated after the quote pick above, which is local
    # and would otherwise burn the wait only to find nothing left to post.
    net_guard.require_network(1800)

    quote   = quote_entry['quote']
    qid     = quote_id(quote)
    speaker = quote_entry.get('speaker', 'narrative')
    book    = quote_entry['book']
    story   = quote_entry.get('story')

    print(f'Quote [{qid}] ({speaker} / {story or book}):')
    print(f'  {quote}')

    # Pick scene — same story, then same book, then any Paget scene
    candidates = pick_images(images, quote_entry, n=6)

    image_entry = None
    image_bytes = None
    for candidate in candidates:
        label = candidate.get('story') or candidate.get('book') or candidate.get('credit_name')
        print(f'Trying: {label} [{candidate.get("source")}]')
        print(f'  URL:  {candidate["image_url"]}')
        if DRY_RUN:
            image_entry = candidate
            break
        try:
            image_bytes = fetch_image(candidate['image_url'])
            image_entry = candidate
            print(f'  OK ({len(image_bytes):,} bytes)')
            break
        except RuntimeError as e:
            print(f'  SKIP: {e}')

    if image_entry is None:
        print('All candidate images failed to fetch. Aborting.')
        sys.exit(1)

    print(f'Image: {image_entry["title"]}')
    print(f'  source={image_entry.get("source")}  story={image_entry.get("story")}  book={image_entry.get("book")}')

    # Format posts. Prefer a single post; only thread if it won't fit.
    combined = build_combined(quote, speaker, book, story, image_entry)
    single = len(combined.build_text()) <= MAX_CHARS

    if single:
        print(f'\nSingle post ({len(combined.build_text())} chars):\n{"-"*40}\n{combined.build_text()}\n{"-"*40}')
    else:
        post1 = build_post1(quote)
        post2 = build_post2(speaker, book, story, image_entry)
        print(f'\nToo long for one post ({len(combined.build_text())} chars) -- threading.')
        print(f'\nPost 1 ({len(post1.build_text())} chars):\n{"-"*40}\n{post1.build_text()}')
        print(f'\nPost 2 ({len(post2.build_text())} chars):\n{"-"*40}\n{post2.build_text()}\n{"-"*40}')

    if DRY_RUN:
        print('(dry run -- not posting)')
        return

    if image_entry.get('source') == 'strand':
        subj = image_entry.get('story') or image_entry.get('book') or 'the Sherlock Holmes stories'
        alt_text = f'Sidney Paget illustration for {subj}, from The Strand Magazine.'
    else:
        alt_text = 'Victorian illustration from the British Library Mechanical Curator collection.'

    # Post to Bluesky -- quote with image, then attribution as reply
    password = keychain_password(HANDLE, KEYCHAIN_SERVICE)
    bsky = Client()
    bsky.login(HANDLE, password)

    if single:
        bsky.send_images(
            text=combined,
            images=[image_bytes],
            image_alts=[alt_text],
        )
    else:
        response = bsky.send_images(
            text=post1,
            images=[image_bytes],
            image_alts=[alt_text],
        )
        root_ref = models.create_strong_ref(response)
        bsky.send_post(
            text=post2,
            reply_to=models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=root_ref),
        )

    print('Posted successfully.')

    # Mark quote as posted (keyed by stable id, not array index)
    posted_ids.add(qid)
    state['posted'] = sorted(posted_ids)
    state['last_success_at'] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    remaining = sum(1 for q in quotes if quote_id(q['quote']) not in posted_ids)
    print(f'Marked [{qid}] as posted. {remaining} quotes remaining.')


if __name__ == '__main__':
    main()
