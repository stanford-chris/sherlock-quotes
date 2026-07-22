#!/usr/bin/env python3
"""
Harvest Sherlock Holmes *scene* illustrations from Wikimedia Commons.

Primary source: Sidney Paget's canonical illustrations for the Strand Magazine
(The Adventures, The Memoirs, The Hound of the Baskervilles, The Return). These
are gaslit-interior / figures-in-action scenes -- the definitive Victorian
Holmes look -- and are public domain (Paget d. 1908). No API key needed.

Optional supplement: British Library "Ghosts & Ghoulish Scenes" from the
Mechanical Curator collection, used by the poster only as occasional atmosphere.

Output: holmes_scenes.json. Each entry:
  {
    "id":          "<commons pageid>",
    "title":       "clean title",
    "book":        "The Adventures of Sherlock Holmes" | null,
    "story":       "A Scandal in Bohemia" | null,
    "source":      "strand" | "british_library",
    "credit_name": "Sidney Paget, The Strand Magazine",
    "page_url":    "https://commons.wikimedia.org/wiki/File:...",
    "image_url":   "https://upload.wikimedia.org/.../1024px-....jpg",  # <=1024px
    "width":  <original width>,
    "height": <original height>
  }

The image_url is a Commons-scaled thumbnail whose width can be rewritten by the
poster (".../1024px-" -> ".../800px-") to stay under Bluesky's ~1 MB blob limit.

Usage:
    python3 holmes_scenes_harvest.py           # full harvest
    python3 holmes_scenes_harvest.py --stats    # summarise existing pool, no fetch
"""

import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

OUTPUT = Path(__file__).parent / 'holmes_scenes.json'
API = 'https://commons.wikimedia.org/w/api.php'
UA = 'HolmesSceneBot/1.0 (https://chris-stanford.com; personal project)'

MIN_EDGE = 600        # skip images whose long edge is under this (grainy thumbnails)
THUMB_W = 1024        # scaled width to request/store
DELAY = 0.4           # polite pause between API calls

# Paget canonical collections. is_novel => every scene is tagged with the book
# as its story (a novel has no sub-stories).
PAGET_SEEDS = [
    ('Category:Illustrations from The Adventures of Sherlock Holmes by Sidney Paget',
     'The Adventures of Sherlock Holmes', False),
    ('Category:Illustrations from The Memoirs of Sherlock Holmes by Sidney Paget',
     'The Memoirs of Sherlock Holmes', False),
    ('Category:Illustrations from The Return of Sherlock Holmes by Sidney Paget',
     'The Return of Sherlock Holmes', False),
    ('Category:The Hound of the Baskervilles illustrated by Sidney Paget',
     'The Hound of the Baskervilles', True),
]

# Optional atmosphere supplement (not Holmes; British Library credit).
# Disabled: the bot is Paget-only by choice (2026-07-18). Flip to True to
# fold the ghoulish scenes back into the pool.
INCLUDE_ATMOSPHERE = False
BL_SEED = 'Category:Ghosts & Ghoulish Scenes from the British Library Mechanical Curator collection'
BL_SAMPLE_CAP = 120   # cap how many ghoulish scenes we keep

# Subcategory names we never treat as canonical English stories.
SKIP_SUBCAT = re.compile(r'avventure|aus den|ars[eè]ne|herlock|welt-detektiv', re.I)


def filepath_url(file_title, width=1000):
    """A width-bounded media URL via Commons Special:FilePath. Reliably stays
    under Bluesky's ~1 MB blob limit regardless of the original's size, and
    works uniformly for JPG and PNG line-art (server renders JPEG)."""
    from urllib.parse import quote
    name = file_title.replace('File:', '')
    return ('https://commons.wikimedia.org/wiki/Special:FilePath/'
            + quote(name) + f'?width={width}')


def get(params):
    params = {**params, 'format': 'json'}
    from urllib.parse import urlencode
    url = API + '?' + urlencode(params)
    for attempt in range(3):
        r = subprocess.run(['curl', '-sS', '-m', '45', '-A', UA, url],
                           capture_output=True, text=True)
        if r.returncode == 0:
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    print(f'  ! request failed: {params.get("cmtitle") or params.get("titles")}')
    return {}


def cat_members(cat, cmtype, limit=500):
    """All members of a category (with continuation)."""
    out, cont = [], {}
    while True:
        data = get({'action': 'query', 'list': 'categorymembers', 'cmtitle': cat,
                    'cmtype': cmtype, 'cmlimit': str(min(limit, 500)), **cont})
        out += [m['title'] for m in data.get('query', {}).get('categorymembers', [])]
        cont = data.get('continue', {})
        if not cont or len(out) >= limit:
            break
        time.sleep(DELAY)
    return out[:limit]


def derive_story(subcat_title):
    """Best-effort canonical story name from a subcategory title, or None."""
    name = subcat_title.replace('Category:', '').strip()
    if SKIP_SUBCAT.search(name):
        return None
    # "Illustrations from" with an "of" variant: Commons is inconsistent, and
    # the "of" spelling hid the entire Norwood Builder subcategory (14 files,
    # 7 of them keepable Strand pages) until 22 Jul 2026.
    m = re.search(r"Illustrations (?:from|of) ['\"]?(.+?)['\"]?,? by ", name)
    if m:
        return m.group(1).strip()
    # Bare story categories, e.g. "The Adventure of the Beryl Coronet"
    if re.match(r'^(The |A |An )', name) and 'Sidney Paget' not in name \
            and 'Sherlock Holmes' not in name and name.isascii():
        return name
    return None


def imageinfo(titles):
    """Batch imageinfo (<=50 titles): scaled url + original size + licence,
    plus the artist/description used to verify the illustrator."""
    info = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        data = get({'action': 'query', 'titles': '|'.join(batch),
                    'prop': 'imageinfo', 'iiprop': 'url|size|extmetadata',
                    'iiurlwidth': str(THUMB_W)})
        for _, pg in data.get('query', {}).get('pages', {}).items():
            ii = (pg.get('imageinfo') or [{}])[0]
            md = ii.get('extmetadata', {}) or {}
            info[pg.get('title')] = {
                'pageid': pg.get('pageid'),
                'width': ii.get('width'), 'height': ii.get('height'),
                'thumburl': ii.get('thumburl') or ii.get('url'),
                'descriptionurl': ii.get('descriptionurl'),
                'license': (md.get('LicenseShortName', {}) or {}).get('value', ''),
                'artist': _plain((md.get('Artist', {}) or {}).get('value', '')),
                'desc': _plain((md.get('ImageDescription', {}) or {}).get('value', '')),
            }
        time.sleep(DELAY)
    return info


def _plain(v):
    """Commons metadata arrives as HTML fragments; reduce to bare text."""
    import html as _html
    return re.sub(r'<[^>]+>', ' ', _html.unescape(str(v or ''))).strip()


# Membership of a "by Sidney Paget" category is NOT evidence that a given file
# is his: Commons files those categories loosely, and the live pool picked up
# 21 foreign-edition plates credited to Paget — among them 8 of 10 Silver Blaze
# scenes and 6 of 7 Beryl Coronet scenes, which are from a French 1913 edition
# drawn by G. Da Fonseca. Others were by Charles R. Macauley (1905 US edition)
# and Frederic Dorr Steele. Since the bot prints "Sidney Paget, The Strand
# Magazine" under every image, attribution has to be evidenced per file.
PAGET_RE = re.compile(r'\bpaget\b', re.I)
STRAND_RE = re.compile(r'strand|newnes', re.I)
# NB: match the "Herlock Sholmes" parody by its surname, never by the substring
# "herlock" — that also matches "Sherlock" and silently rejects the entire pool.
FOREIGN_EDITION_RE = re.compile(
    r'premi[eè]res aventures|avventure|aus den|aventuras|шість|наполє'
    r'|czarny piotr|welt-detektiv|\bsholmes\b', re.I)


# Strings that appear in Artist fields but make no claim about the illustrator:
# the author, the Strand's publisher, and placeholder values. A file credited
# only to these tells us nothing either way, so it falls back to the evidence of
# sitting in a "by Sidney Paget" category.
NON_ILLUSTRATOR_RE = re.compile(
    r'\b(a\.?\s*)?conan\s+doyle\b|\barthur\b|\bdoyle\b|\bnewnes\b|\bpublisher\b'
    r'|\bstrand\b|\bmagazine\b|unknown|\banonymous\b|\bn/?a\b|\bs\.?\s*p\.?\b'
    r'|\bgeorge\b|\bltd\b|\boriginal\b|\bauthor\b|\bscan\b|\buser\b'
    # Role labels: "Illustrator: Unknown" must not read as a person called
    # Illustrator (it cost a keepable 784x824 Norwood scene). A label followed
    # by a real name still rejects — only the label itself is stripped.
    r'|\b(editor|illustrator|artist|engraver)\b'
    # Stopwords, so a leftover "The" is not mistaken for a surname.
    r'|\b(the|and|for|from|via|out|its|his|her)\b', re.I)


def is_paget(title, meta):
    """True unless this file is evidenced as someone else's work.

    Rejecting anything that fails to *name* Paget is too harsh: plenty of
    genuine Strand plates carry only "Publisher G. Newnes" or "Arthur Conan
    Doyle" in the Artist field, and dropping those cost two-thirds of the pool
    (including 21 of 32 Hound scenes). So the test is inverted — a file is
    disqualified when its metadata names an illustrator who is not Paget."""
    artist, desc = meta.get('artist', ''), meta.get('desc', '')
    blob = ' '.join((title, artist, desc))
    if FOREIGN_EDITION_RE.search(blob):
        return False
    if PAGET_RE.search(blob):
        return True
    # Strip the author/publisher/placeholder noise; anything name-like left over
    # is a competing illustrator (Da Fonseca, Macauley, Steele, ...).
    residue = NON_ILLUSTRATOR_RE.sub(' ', artist)
    residue = re.sub(r'[^A-Za-z]+', ' ', residue)
    if any(len(w) > 2 for w in residue.split()):
        return False
    return True


def is_public_domain(license_str):
    """Keep only clearly public-domain files. Rejects CC BY-SA etc. (an uploader
    can claim a licence on their own scan of PD art); an empty tag is allowed
    since membership in a 'by Sidney Paget' category vouches for the work."""
    low = (license_str or '').lower()
    if not low:
        return True
    return any(k in low for k in
               ('public domain', 'pd', 'cc0', 'no restrictions', 'expired'))


def clean_title(title):
    t = title.replace('File:', '')
    t = re.sub(r'\.\w+$', '', t)                 # drop extension
    t = re.sub(r'\s*\(\d{6,}\)\s*', ' ', t)      # drop flickr/id numbers
    return re.sub(r'\s+', ' ', t).strip()


def add_entry(pool, title, meta, book, story, source, credit):
    """Insert/merge one entry, keyed by pageid; upgrade story if we learn it."""
    pid = meta.get('pageid')
    w, h = meta.get('width') or 0, meta.get('height') or 0
    if not pid or max(w, h) < MIN_EDGE or not meta.get('thumburl'):
        return False
    if not is_public_domain(meta.get('license')):
        return False
    if source == 'strand' and not is_paget(title, meta):
        return False
    if pid in pool:
        if story and not pool[pid]['story']:
            pool[pid]['story'] = story
        return False
    page_url = meta.get('descriptionurl') or (
        'https://commons.wikimedia.org/wiki/' + title.replace(' ', '_'))
    pool[pid] = {
        'id': str(pid), 'title': clean_title(title), 'file': title,
        'book': book, 'story': story, 'source': source,
        'credit_name': credit, 'page_url': page_url,
        'image_url': filepath_url(title, 1000), 'width': w, 'height': h,
    }
    return True


def main():
    if '--stats' in sys.argv:
        entries = json.loads(OUTPUT.read_text())
        print(f'Pool: {len(entries)} scenes')
        print('\nBy source:')
        for s, c in Counter(e['source'] for e in entries).most_common():
            print(f'  {c:4d}  {s}')
        print('\nBy book:')
        for b, c in Counter(e['book'] for e in entries).most_common():
            print(f'  {c:4d}  {b}')
        with_story = sum(1 for e in entries if e['story'])
        print(f'\nWith a story tag: {with_story}/{len(entries)}')
        print('\nStories covered:')
        for st, c in Counter(e['story'] for e in entries if e['story']).most_common():
            print(f'  {c:3d}  {st}')
        return

    pool = {}

    # ---- Paget canonical scenes ----
    for cat, book, is_novel in PAGET_SEEDS:
        credit = 'Sidney Paget, The Strand Magazine'
        default_story = book if is_novel else None
        files = cat_members(cat, 'file')
        info = imageinfo(files) if files else {}
        kept = sum(add_entry(pool, t, info.get(t, {}), book, default_story,
                             'strand', credit) for t in files)
        print(f'{book}: {len(files)} files, {kept} kept  [{cat.split(":",1)[1][:40]}]')

        for sub in cat_members(cat, 'subcat'):
            story = derive_story(sub)
            if not story:
                print(f'    (skipped subcat: {sub.replace("Category:","")[:50]})')
                continue
            sfiles = cat_members(sub, 'file')
            sinfo = imageinfo(sfiles) if sfiles else {}
            skept = sum(add_entry(pool, t, sinfo.get(t, {}), book, story,
                                 'strand', credit) for t in sfiles)
            print(f'    story "{story}": {len(sfiles)} files, {skept} kept')

    strand_count = len(pool)
    print(f'\nStrand/Paget scenes kept: {strand_count}')

    # ---- Optional British Library atmosphere (off by default; Paget-only) ----
    if INCLUDE_ATMOSPHERE:
        bl_files = cat_members(BL_SEED, 'file', limit=BL_SAMPLE_CAP * 3)
        bl_info = imageinfo(bl_files) if bl_files else {}
        bl_kept = 0
        for t in bl_files:
            if bl_kept >= BL_SAMPLE_CAP:
                break
            if add_entry(pool, t, bl_info.get(t, {}), None, None, 'british_library',
                         'British Library (Mechanical Curator collection)'):
                bl_kept += 1
        print(f'British Library atmosphere scenes kept: {bl_kept}')

    entries = list(pool.values())
    OUTPUT.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    print(f'\nSaved {len(entries)} scenes to {OUTPUT}')


if __name__ == '__main__':
    main()
