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

import image_alt
import alt_log
import net_guard

QUOTES_FILE  = Path(__file__).parent / 'holmes_quotes.json'
IMAGES_FILE  = Path(__file__).parent / 'holmes_scenes.json'
# Victorian British photographs: the art for every quote whose own work Paget
# never illustrated, which is 76% of the pool. See load_photos and pick_images.
PHOTOS_FILE  = Path(__file__).parent / 'holmes_images.json'
STATE_FILE   = Path(__file__).parent / 'holmes_state.json'
# One JSONL line per posted quote, recording the alt text that shipped and
# whether it was generated or fell back. Written only on a real post, and
# best-effort: see alt_log.
ALT_LOG      = Path(__file__).parent / 'alt_history.jsonl'

HANDLE           = 'sherlockquotes.bsky.social'
KEYCHAIN_SERVICE = 'holmesbot-bluesky'

# Shared with the other bots on this machine: one long-lived `claude setup-token`
# rather than the interactive login's short-lived token, which expires intra-day
# and 401s a headless launchd run. Used only to describe the illustration for
# alt text, so its absence degrades the alt and never blocks the post.
CLAUDE_TOKEN_ACCOUNT = 'seoulbot'
CLAUDE_TOKEN_SERVICE = 'claude-oauth-token'

# Refuse anything unrecognised. Until August 2026 this was a bare membership
# test, so an unknown flag (`--help` above all) fell through to a LIVE post:
# the same trap that published a real thread from seoul-index on 20 Jul 2026.
_KNOWN_ARGS = {'--dry-run', '--tail'}


def _tail_n(argv):
    """N for `--tail [N]` (print recent alt text and exit), or None if absent.
    N defaults to 10 and a bare integer right after --tail overrides it."""
    if '--tail' not in argv:
        return None
    i = argv.index('--tail')
    if i + 1 < len(argv) and argv[i + 1].isdigit():
        return max(1, int(argv[i + 1]))
    return 10


if __name__ == '__main__':
    _skip = None
    if '--tail' in sys.argv:
        _t = sys.argv.index('--tail')
        if _t + 1 < len(sys.argv) and sys.argv[_t + 1].isdigit():
            _skip = _t + 1
    _unknown = [a for j, a in enumerate(sys.argv[1:], 1)
                if a not in _KNOWN_ARGS and j != _skip]
    if _unknown:
        sys.exit(f'Unknown argument(s): {" ".join(_unknown)}. '
                 f'Recognised: {" ".join(sorted(_KNOWN_ARGS))} [N]. '
                 f'Refusing to run (a bare run posts live).')

DRY_RUN          = '--dry-run' in sys.argv
TAIL_N           = _tail_n(sys.argv)

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

def claude_env():
    """Env for the `claude -p` subprocess used to describe the illustration.

    Injects the long-lived Keychain token as CLAUDE_CODE_OAUTH_TOKEN when one is
    stored, and otherwise falls back to the ambient environment so a manual run
    with a logged-in CLI still works.
    """
    env = os.environ.copy()
    r = subprocess.run(
        ['security', 'find-generic-password',
         '-a', CLAUDE_TOKEN_ACCOUNT, '-s', CLAUDE_TOKEN_SERVICE, '-w'],
        capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        env['CLAUDE_CODE_OAUTH_TOKEN'] = r.stdout.strip()
    return env


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


# Shortest quote that can carry a post on its own. Lowered from 60 on 18 Aug
# 2026 to unblock Holmes's shorter lines: at 60 the eligible dialogue pool was
# 37 quotes, at 40 it is 127. The floor is deliberately not per-speaker, because
# it does not need to be. Watson's narrative is harvested a paragraph at a time
# (MIN_PROSE_LEN in holmes_harvest.py) and the shortest narrative quote in the
# pool is 60 characters, so this bound only ever bites on dialogue. Going below
# 40 buys nothing either: MIN_LEN in the harvester means no shorter span exists.
MIN_QUOTE_LEN = 40


def is_complete_quote(text):
    """True if the quote is a complete, standalone sentence."""
    return (
        text.endswith(('.', '!', '?'))
        and len(text) >= MIN_QUOTE_LEN
        and text[0].isupper()
    )


def pick_quote(quotes, posted_ids):
    unposted = [
        q for q in quotes
        if quote_id(q['quote']) not in posted_ids and is_complete_quote(q['quote'])
    ]
    if not unposted:
        return None
    # No art bias. Until 18 Aug 2026 a third of draws were restricted to the
    # 15 works Paget illustrated, to lift the share of posts whose art really
    # was the scene. Photographs now cover every other work, so the reason is
    # gone and the whole canon draws evenly again: the bias had been pulling
    # the feed towards Hound, Adventures and Memoirs.
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


# The four novels are each a single work; a collection is an anthology of many,
# so a quote or a scene tagged only with a collection has no known work.
NOVELS = {
    'A Study in Scarlet',
    'The Sign of the Four',
    'The Hound of the Baskervilles',
    'The Valley of Fear',
}


def _work_key(story, book):
    """Fuzzy key for the work a quote or scene belongs to: its story, or for
    the four novels the novel itself. A collection entry carrying no story
    returns None, so it never matches: 37 of the 225 Paget scenes are tagged
    with a collection but no story, and treating those as work-level matches
    would readmit exactly the cross-story art this rule exists to stop."""
    if story:
        return _match_key(story)
    if book in NOVELS:
        return _match_key(book)
    return None


# 'London' as a surname or a ship, not the city. The LOC name authority files
# Jack London under the subject heading 'london, jack', so a subject match
# alone lets his portraits through. Rep. Meyer London is the other repeat.
_LONDON_NOT_THE_CITY = re.compile(
    r"\blondon,\s*(?:jack|meyer)\b|\b(?:jack|meyer)\s+london\b", re.IGNORECASE)

# LOC subject terms for media that are not photographs. The pool is harvested
# by place rather than by medium, so it carries etchings, lithographs, drawings,
# coins and gallery reproductions of paintings alongside the photographs: 140 of
# 797 when measured 18 Aug 2026, about one post in seven. Each would ship under
# a camera credit, in the lane this bot reserves for photographs, contradicting
# both the credit line and the profile. Paget already holds the illustration
# lane; an 1890s etching of New Oxford Street is good art, but not here.
#
# Bare 'prints' is safe to exclude on: it never co-occurs with a photographic
# format term in this pool, so it is a medium in its own right, not a parent of
# 'photographic prints'.
NOT_A_PHOTOGRAPH = {
    'coins', 'drawings', 'engravings', 'etchings', 'illustrations', 'lithographs',
    'manuscripts', 'medals', 'paintings', 'posters', 'prints', 'reproductions',
    'sculpture', 'woodcuts',
}

# Match 'london' except in 'New London' (Connecticut).
_LONDON_RE = re.compile(r'(?<!new )london', re.IGNORECASE)


def is_british(img):
    """True if a LOC photo is confirmed British by its subject headings.

    Subject headings only: they are structured authority terms and hold up.
    Titles do not. Measured 18 Aug 2026 over the 2,955 non-stereo photos, 171
    passed on their title alone and roughly four in five of those were duds:
    trademark registrations for London-brand gin and hats, theatre bills for
    London hits playing New York, Boston's 'London Honorables', the London
    Hosiery Mills of Loudon, Tennessee, and East London, South Africa.
    """
    subjects = ' '.join(img.get('subjects', []))
    if _LONDON_NOT_THE_CITY.search(subjects) or _LONDON_NOT_THE_CITY.search(img.get('title', '')):
        return False
    return 'england' in subjects.lower() or bool(_LONDON_RE.search(subjects))


def clean_image_title(title):
    """Strip redundant ', England' when London is already in the title."""
    if re.search(r'\blondon\b', title, re.IGNORECASE):
        title = re.sub(r',?\s*England', '', title, flags=re.IGNORECASE).strip()
    return title


def load_photos(path):
    """Victorian British photographs from the Library of Congress, normalised
    into a scene entry's shape so the credit and alt-text paths branch on
    nothing but `source`.

    Stereo cards and panoramas are dropped: both are very wide and render
    badly at Bluesky's aspect ratios. Non-photographic media are dropped too,
    see NOT_A_PHOTOGRAPH. The British filter is is_british. Yield on 18 Aug
    2026: 5,339 harvested to 657 usable, dated 1870s to 1910s and concentrated
    in the 1890s and 1900s.
    """
    if not path.exists():
        return []
    photos = []
    for img in json.loads(path.read_text()):
        if any('stereo' in s.lower() for s in img.get('subjects', [])):
            continue
        url = img.get('image_url', '')
        if '/stereo/' in url or '/pan/' in url:
            continue
        if NOT_A_PHOTOGRAPH & set(img.get('subjects', [])):
            continue
        if not is_british(img):
            continue
        photos.append({
            **img,
            'source': 'loc',
            'story': None,
            'book': None,
            'credit_name': 'Library of Congress',
            'page_url': img.get('id', ''),
            'title': clean_image_title(img.get('title', '')),
        })
    return photos


def pick_images(scenes, photos, quote_entry, n=6):
    """Ordered art candidates for a quote: Paget when he illustrated the quote's
    own work, a photograph otherwise.

    The Strand pool is 225 illustrations covering 15 works. Until 18 Aug 2026
    the unmatched majority fell through to art from a *different* story,
    labelled honestly but still not the scene the quote came from.
    Measured that day over the 2,470-quote pool: 593 quotes (24%) have art for
    their own work; the remaining 1,877 (76%) do not, and 1,392 of those come
    from the five books with no Paget art here at all (The Valley of Fear,
    A Study in Scarlet, The Case-Book, His Last Bow, The Sign of the Four).
    """
    strand = [s for s in scenes if s.get('source') == 'strand']
    atmos  = [s for s in scenes if s.get('source') == 'british_library']

    key = _work_key(quote_entry.get('story'), quote_entry.get('book'))
    if key:
        matched = [s for s in strand
                   if _work_key(s.get('story'), s.get('book')) == key]
        if matched:
            random.shuffle(matched)
            return matched[:n]

    candidates = []
    # Atmosphere art leads for a suitably eerie quote. Inert as things stand:
    # INCLUDE_ATMOSPHERE is False in the scenes harvester, so the pool holds no
    # british_library entries and this branch never fires.
    if atmos and is_atmospheric(quote_entry['quote']) and random.random() < 0.25:
        picks = atmos[:]
        random.shuffle(picks)
        candidates.extend(picks[:2])

    rest = [p for p in photos if p not in candidates]
    random.shuffle(rest)
    candidates.extend(rest)
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


def append_attribution(tb, speaker, book, story, image_entry):
    """Append the attribution + photo credit to an existing TextBuilder."""
    book_url, book_emoji = BOOK_META.get(book, (None, '\U0001f4da'))
    # A collection story uses its own emoji; novels and unmapped/story-less
    # quotes fall back to the collection emoji.
    emoji = STORY_EMOJI.get(_story_key(story), book_emoji) if story else book_emoji
    credit_name = image_entry.get('credit_name', 'Wikimedia Commons')
    page_url = image_entry.get('page_url', '')
    # Pen nib for a Paget illustration, camera for a photograph.
    credit_emoji = '\U0001f4f7' if image_entry.get('source') == 'loc' else '\u2712\ufe0f'

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
    tb.text(f' {emoji}\n\n{credit_emoji} ')
    if page_url:
        tb.link(credit_name, page_url)
    else:
        tb.text(credit_name)
    # Photographs carry their year. Illustration dates are book-level rather
    # than per-image, so a Paget scene shows none.
    img_date = image_entry.get('date')
    if image_entry.get('source') == 'loc' and img_date:
        tb.text(f' ({img_date})')
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
    # --tail is a read-only viewer: print recent alt text and exit before the
    # quote pool, the network or any post. Never touches state.
    if TAIL_N is not None:
        alt_log.tail(ALT_LOG, TAIL_N)
        return

    quotes = json.loads(QUOTES_FILE.read_text())
    images = json.loads(IMAGES_FILE.read_text())
    photos = load_photos(PHOTOS_FILE)
    state  = load_state()
    posted_ids = set(state.get('posted', []))

    # Pick quote
    quote_entry = pick_quote(quotes, posted_ids)
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

    # Pick art: Paget when he illustrated this very work, a photograph otherwise
    candidates = pick_images(images, photos, quote_entry, n=6)

    image_entry = None
    image_bytes = None
    for candidate in candidates:
        label = (candidate.get('story') or candidate.get('book')
                 or candidate.get('title') or candidate.get('credit_name'))
        print(f'Trying: {label} [{candidate.get("source")}]')
        print(f'  URL:  {candidate["image_url"]}')
        # A dry run used to stop at the URL and never download. Now that alt
        # text is generated FROM the image, skipping the fetch would leave the
        # part most worth reviewing unreviewable, so a dry run fetches too.
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

    # Alt text describes the SCENE, with attribution as a short tail.
    #
    # Until August 2026 the alt was attribution alone: who drew it and where it
    # appeared. That tells a blind reader nothing about the illustration, and
    # the story name it carried was already in the post. The non-Paget branch
    # was worse still, an identical fixed string on every such post.
    #
    # image_alt.describe() shows the model the actual illustration. Best-effort:
    # any failure falls back to the attribution string, which is a truthful
    # caption even if a poor description, so a post never fails over alt text.
    subj = ''
    if image_entry.get('source') == 'strand':
        subj = image_entry.get('story') or image_entry.get('book') or 'the Sherlock Holmes stories'
        attribution = f'Sidney Paget illustration for {subj}, from The Strand Magazine.'
    elif image_entry.get('source') == 'loc':
        title = image_entry.get('title', '')
        date = image_entry.get('date')
        attribution = (f'{title}, {date}. Library of Congress.' if date
                       else f'{title}. Library of Congress.')
    else:
        attribution = 'Victorian illustration from the British Library Mechanical Curator collection.'

    # A photograph's LOC title is the whole of its context, and subj repeats it.
    parts = [image_entry.get('title', '')]
    if subj and subj != image_entry.get('title', ''):
        parts.append(subj)
    context = ' / '.join(p for p in parts if p)
    desc = image_alt.describe(image_bytes, context=context, env=claude_env())
    # Attribution FIRST, then the disclosure, then the description.
    #
    # Until 18 Aug 2026 the disclosure led the whole string and the attribution
    # trailed it, so a listener heard "A.I.-generated description." as a header
    # over everything that followed, the Library of Congress's own catalogue
    # title and date included. That labels a human-catalogued fact as model
    # output, which is the exact inverse of what the disclosure is for. Leading
    # with the attribution puts the trustworthy statement first and leaves the
    # disclosure adjacent to the only text it covers.
    #
    # The disclosure still rides the generated branch only: with no description
    # the alt is the attribution alone, which is human provenance throughout.
    alt_text = (f'{attribution} {image_alt.DISCLOSURE} {desc}'
                if desc else attribution)
    print(f'\nAlt ({len(alt_text)} chars):\n{"-"*40}\n{alt_text}\n{"-"*40}')

    # The dry run stops HERE rather than before the alt is built. Alt text
    # became generated content in August 2026, so previewing a post without it
    # would leave the half most worth checking unreviewed.
    if DRY_RUN:
        print('(dry run -- not posting)')
        return

    # Post to Bluesky -- quote with image, then attribution as reply
    password = keychain_password(HANDLE, KEYCHAIN_SERVICE)
    bsky = Client()
    bsky.login(HANDLE, password)

    if single:
        response = bsky.send_images(
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

    # Record what shipped. `generated` is the flag worth watching: a failed
    # model call falls back to the attribution string silently by design, which
    # keeps posts going out and makes a run of failures invisible. A tail
    # showing every recent post falling back means the description step is
    # broken, not that the illustrations resist description.
    alt_log.append(ALT_LOG, {
        'at': datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S'),
        'id': qid,
        'title': story or book,
        'url': alt_log.post_url(HANDLE, getattr(response, 'uri', None)),
        'generated': desc is not None,
        'alts': [alt_text],
    })

    # Mark quote as posted (keyed by stable id, not array index)
    posted_ids.add(qid)
    state['posted'] = sorted(posted_ids)
    state['last_success_at'] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    remaining = sum(1 for q in quotes if quote_id(q['quote']) not in posted_ids)
    print(f'Marked [{qid}] as posted. {remaining} quotes remaining.')


if __name__ == '__main__':
    main()
