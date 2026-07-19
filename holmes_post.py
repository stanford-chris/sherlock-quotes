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
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from atproto import Client, client_utils, models

QUOTES_FILE  = Path(__file__).parent / 'holmes_quotes.json'
IMAGES_FILE  = Path(__file__).parent / 'holmes_images.json'
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

TAGS = [
    ('SherlockHolmes', 'SherlockHolmes'),
    ('ConanDoyle',     'ConanDoyle'),
    ('Victorian',      'Victorian'),
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

# Quote keywords → image tags for matching
# Each entry: (keywords_in_quote, image_tag)
QUOTE_TO_IMAGE_TAG = [
    (['river', 'thames', 'stream', 'water', 'boat', 'barge', 'tide', 'bank', 'shore',
      'embankment', 'wharf', 'current', 'flood', 'rowing'],          'river'),
    (['bridge', 'span'],                                               'bridge'),
    (['street', 'road', 'pavement', 'lane', 'alley', 'corner',
      'cab', 'hansom', 'omnibus', 'carriage', 'traffic', 'crowd',
      'gutter', 'kerb', 'footstep'],                                  'street'),
    (['church', 'cathedral', 'abbey', 'chapel', 'steeple', 'bell',
      'prayer', 'sermon', 'burial', 'grave', 'tomb'],                 'church'),
    (['tower', 'turret', 'battlement', 'fortress', 'castle',
      'rampart'],                                                      'tower'),
    (['park', 'garden', 'lawn', 'tree', 'grass', 'leaf', 'branch',
      'flower', 'bush', 'shrub', 'wood', 'forest'],                   'park'),
    (['fog', 'mist', 'haze', 'smoke', 'murk', 'damp', 'grey',
      'gray', 'gloom', 'shadow', 'dark', 'dusk', 'twilight',
      'candle', 'lamp', 'lantern'],                                   'fog'),
    (['night', 'midnight', 'moon', 'star', 'dawn', 'morning',
      'evening'],                                                      'night'),
    (['market', 'shop', 'stall', 'vendor', 'merchant', 'trade',
      'wares', 'goods'],                                               'market'),
    (['palace', 'mansion', 'manor', 'estate', 'hall'],                'palace'),
    (['parliament', 'government', 'minister', 'political'],           'parliament'),
    (['dock', 'port', 'harbour', 'harbor', 'quay', 'vessel',
      'ship', 'steamer'],                                              'dock'),
    (['station', 'train', 'railway', 'platform', 'carriage',
      'locomotive'],                                                   'station'),
    (['room', 'chamber', 'study', 'library', 'fireplace', 'hearth',
      'armchair', 'table', 'mantelpiece', 'shelf'],                   'interior'),
    (['portrait', 'face', 'eyes', 'expression', 'features',
      'countenance'],                                                  'portrait'),
]


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
    STATE_FILE.write_text(json.dumps(state, indent=2))


def is_complete_quote(text):
    """True if the quote is a complete, standalone sentence."""
    return (
        text.endswith(('.', '!', '?'))
        and len(text) >= 60
        and text[0].isupper()
    )


def pick_quote(quotes, posted_ids):
    unposted = [
        q for q in quotes
        if quote_id(q['quote']) not in posted_ids and is_complete_quote(q['quote'])
    ]
    if not unposted:
        return None
    # Dialogue is ~10% of the pool; post it 40% of the time to over-represent it for variety.
    dialogue = [q for q in unposted if q.get('speaker') != 'narrative']
    narrative = [q for q in unposted if q.get('speaker') == 'narrative']
    if dialogue and (not narrative or random.random() < 0.4):
        return random.choice(dialogue)
    return random.choice(narrative)


def infer_image_tags(quote_text):
    """Return a list of image tags inferred from keywords in the quote.

    Uses word-boundary matching so a short keyword doesn't fire inside a longer
    word (e.g. 'river' must not match inside 'driver', 'bank' not in 'banker')."""
    lower = quote_text.lower()
    matched = []
    for keywords, tag in QUOTE_TO_IMAGE_TAG:
        if any(re.search(rf'\b{re.escape(kw)}\b', lower) for kw in keywords):
            matched.append(tag)
    return matched


def pick_images(images, desired_tags, n=5):
    """Return up to n candidate images (best-match first) to try in order."""
    # Exclude stereo card images (very wide format, renders poorly on Bluesky)
    images = [
        img for img in images
        if not any('stereo' in s.lower() for s in img.get('subjects', []))
        and '/stereo/' not in img.get('image_url', '')
        and '/pan/' not in img.get('image_url', '')
    ]

    def is_british(img):
        # Match 'london' only when not preceded by 'new' (excludes New London, Conn.)
        london_re = re.compile(r'(?<!new )london', re.IGNORECASE)
        subjects = ' '.join(img.get('subjects', []))
        title = img.get('title', '')
        return ('england' in subjects.lower()
                or london_re.search(subjects)
                or london_re.search(title))

    # Filter to images confirmed as British to exclude noise
    london_images = [img for img in images if is_british(img)]
    pool = london_images or images  # fall back if filter is too aggressive

    candidates = []
    if desired_tags:
        matched = [img for img in pool if any(t in img.get('tags', []) for t in desired_tags)]
        random.shuffle(matched)
        candidates.extend(matched)

    # Fill remainder with random picks from pool (no duplicates)
    remaining = [img for img in pool if img not in candidates]
    random.shuffle(remaining)
    candidates.extend(remaining)

    return candidates[:n]


def clean_image_title(title):
    """Strip redundant ', England' when London is already in the title."""
    if re.search(r'\blondon\b', title, re.IGNORECASE):
        title = re.sub(r',?\s*England', '', title, flags=re.IGNORECASE).strip()
    return title


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


def append_attribution(tb, speaker, book, story, image_entry):
    """Append the attribution + photo credit to an existing TextBuilder."""
    book_url, book_emoji = BOOK_META.get(book, (None, '\U0001f4da'))
    # A collection story uses its own emoji; novels and unmapped/story-less
    # quotes fall back to the collection emoji.
    emoji = STORY_EMOJI.get(_story_key(story), book_emoji) if story else book_emoji
    img_date = image_entry['date']
    img_page = image_entry.get('id', '')

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
    tb.text(f' {emoji}\n\n\U0001f4f7 ')
    if img_page:
        tb.link('Library of Congress', img_page)
    else:
        tb.text('Library of Congress')
    tb.text(f' ({img_date})')
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


def fetch_image(url):
    result = subprocess.run(
        ['curl', '-s', '--http1.1', '--max-time', '30', '-o', '-', url],
        capture_output=True
    )
    if result.returncode != 0 or len(result.stdout) < 1000:
        raise RuntimeError(f'Failed to fetch image: {url}')
    return result.stdout


def main():
    quotes = json.loads(QUOTES_FILE.read_text())
    images = json.loads(IMAGES_FILE.read_text())
    state  = load_state()
    posted_ids = set(state.get('posted', []))

    # Pick quote
    quote_entry = pick_quote(quotes, posted_ids)
    if quote_entry is None:
        print('All quotes have been posted. Reset holmes_state.json to restart.')
        sys.exit(0)

    quote   = quote_entry['quote']
    qid     = quote_id(quote)
    speaker = quote_entry.get('speaker', 'narrative')
    book    = quote_entry['book']
    story   = quote_entry.get('story')

    print(f'Quote [{qid}] ({speaker} / {story or book}):')
    print(f'  {quote}')

    # Pick image — try up to 5 candidates until one fetches successfully
    desired_tags = infer_image_tags(quote)
    print(f'Desired image tags: {desired_tags or ["(any)"]}"')
    candidates = pick_images(images, desired_tags, n=5)

    image_entry = None
    image_bytes = None
    for candidate in candidates:
        print(f'Trying: {candidate["title"]} ({candidate["date"]})')
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

    print(f'Image: {image_entry["title"]} ({image_entry["date"]})')
    print(f'  Tags: {image_entry["tags"]}')

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

    alt_text = f'{image_entry["title"]}, {image_entry["date"]}. Library of Congress.'

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
