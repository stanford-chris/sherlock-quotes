#!/usr/bin/env python3
"""
Harvest quotes and prose passages from the Sherlock Holmes canon.

Extracts:
  1. Dialogue attributed to Holmes and other major characters
  2. Watson's narrative prose (non-dialogue paragraphs)

Saves to holmes_quotes.json. Each entry has:
  { "quote": "...", "book": "...", "speaker": "Holmes|Watson|Mycroft|...",
    "story": "..." }
  Speaker is "narrative" for prose passages.
  Story is the individual story within a collection, or null for the 4 novels
  (and for the rare quote that can't be located within a story).

Re-running is safe: by default a harvest MERGES into the existing file, keeping
every quote already there (and the posted-state in holmes_state.json that refers
to them by text-hash) and only appending genuinely new quotes. The previous file
is backed up and the write is atomic.

Usage:
    python3 holmes_harvest.py            # harvest and merge into the existing file
    python3 holmes_harvest.py --replace  # replace the pool instead of merging (still backs up)
    python3 holmes_harvest.py --stats    # print counts per book/speaker, no save
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

OUTPUT = Path(__file__).parent / 'holmes_quotes.json'
KEEP_BACKUPS = 5   # timestamped backups to retain; older ones are pruned

# Project Gutenberg plain-text URLs for the canon
BOOKS = [
    ('A Study in Scarlet',                'https://www.gutenberg.org/files/244/244-0.txt'),
    ('The Sign of the Four',              'https://www.gutenberg.org/files/2097/2097-0.txt'),
    ('The Adventures of Sherlock Holmes', 'https://www.gutenberg.org/files/1661/1661-0.txt'),
    ('The Memoirs of Sherlock Holmes',    'https://www.gutenberg.org/files/834/834-0.txt'),
    ('The Return of Sherlock Holmes',     'https://www.gutenberg.org/files/108/108-0.txt'),
    ('The Hound of the Baskervilles',     'https://www.gutenberg.org/files/2852/2852-0.txt'),
    ('The Valley of Fear',                'https://www.gutenberg.org/files/3289/3289-0.txt'),
    ('His Last Bow',                      'https://www.gutenberg.org/files/2350/2350-0.txt'),
    ('The Case-Book of Sherlock Holmes',  'https://www.gutenberg.org/files/69700/69700-0.txt'),
]

# Characters whose dialogue to extract
CHARACTERS = [
    'Holmes',
    'Watson',
    'Mycroft',
    'Moriarty',
    'Irene',       # Irene Adler
    'Lestrade',
    'Moran',       # Colonel Moran
    'Irene Adler',
]

# Verbs used to attribute dialogue
ATTR_VERBS = (
    r'said|cried|replied|answered|remarked|observed|continued|exclaimed|'
    r'muttered|growled|snapped|asked|added|returned|laughed|smiled|chuckled|'
    r'interrupted|protested|urged|suggested|mused|called|shouted|whispered'
)

# Gutenberg files use either curly quotes or straight quotes depending on edition
# “ = left double quote, ” = right double quote, \x22 = straight quote
OQ = '[“\x22]'
CQ = '[”\x22]'
NON_CQ = '[^”\x22]'

# Gutenberg boilerplate phrases to skip
SKIP_PHRASES = [
    'project gutenberg', 'gutenberg', 'ebook', 'electronic', 'copyright',
    'license', 'www.', 'http', 'produced by',
]

# Phrases that signal plot mechanics in prose (applied to narrative only, not dialogue)
PROSE_SKIP = [
    'chapter',
    # Dialogue attribution fragments
    'said he', 'said she', 'said i', 'i said', 'he said', 'she said',
    'i asked', 'he asked', 'she asked', 'replied', 'answered', 'exclaimed',
    # Character names (sentences naming characters tend to be plot narration)
    'sherlock holmes', 'mr. holmes', 'dr. watson', 'inspector lestrade',
    'colonel', 'mrs. hudson', 'irene adler',
    # Common plot-mechanical openers
    'we drove', 'we walked', 'we arrived', 'we entered', 'we found',
    'we left', 'we returned', 'we took', 'we went', 'we passed',
    'he walked', 'he entered', 'he turned', 'he rose', 'he sat',
    'he took', 'he drew', 'he handed', 'he produced', 'he pointed',
    'i followed', 'i found', 'i took', 'i turned', 'i entered',
    'i heard', 'i saw', 'i noticed', 'i watched', 'i waited',
]


def should_skip_prose(text):
    lower = text.lower()
    if any(phrase in lower for phrase in SKIP_PHRASES):
        return True
    if any(phrase in lower for phrase in PROSE_SKIP):
        return True
    if text.startswith('[') or text.startswith('('):
        return True
    return False

MIN_LEN = 40    # characters
MAX_LEN = 280   # Bluesky post limit
MIN_PROSE_LEN = 100
MAX_PROSE_LEN = 260

# Minimum words for a prose sentence to feel complete
MIN_PROSE_WORDS = 8

# Sentences starting with these words are nearly always plot narration
PRONOUN_START = re.compile(
    r'^(he|she|i|we|they|it|his|her|their|our|my|its)\b', re.IGNORECASE
)

# Proper nouns whose presence signals plot-mechanical rather than atmospheric prose
PROSE_PROPER_NOUNS = [
    'holmes', 'watson', 'lestrade', 'mycroft', 'moriarty', 'irene', 'adler',
    'baker street', 'scotland yard',
    'mrs.', 'mr.', 'dr.', 'lord ', 'lady ',
]


def build_dialogue_patterns(name):
    """Return compiled regex patterns for dialogue attributed to `name`."""
    n = re.escape(name)
    return [
        # "quote," said Name
        re.compile(
            OQ + r'(' + NON_CQ + r'{20,280})' + CQ + r'[,.]?\s+(?:' + ATTR_VERBS + r')\s+' + n + r'[,.]?',
            re.IGNORECASE
        ),
        # Name said, "quote"
        re.compile(
            r'\b' + n + r'\s+(?:' + ATTR_VERBS + r')[,.]?\s+' + OQ + r'(' + NON_CQ + r'{20,280})' + CQ,
            re.IGNORECASE
        ),
        # "quote," Name said
        re.compile(
            OQ + r'(' + NON_CQ + r'{20,280})' + CQ + r'[,.]?\s+' + n + r'\s+(?:' + ATTR_VERBS + r')[,.]?',
            re.IGNORECASE
        ),
    ]


DIALOGUE_PATTERNS = {name: build_dialogue_patterns(name) for name in CHARACTERS}


def fetch(url):
    result = subprocess.run(
        ['curl', '-s', '--max-time', '30',
         '-H', 'User-Agent: Holmes-Quote-Bot/1.0 (personal project)',
         url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f'curl failed: {result.stderr}')
    return result.stdout


def clean_text(text):
    text = re.sub(r'\s+', ' ', text).strip()
    # Normalise curly apostrophes and quotes to straight
    text = text.replace('‘', "'").replace('’', "'")
    text = text.replace('“', '"').replace('”', '"')
    # Strip leading/trailing punctuation fragments
    text = text.strip(',-')
    return text.strip()


def should_skip(text):
    lower = text.lower()
    if any(phrase in lower for phrase in SKIP_PHRASES):
        return True
    if text.startswith('[') or text.startswith('('):
        return True
    return False


def is_dialogue_para(para):
    """True if the paragraph contains quoted speech."""
    return bool(re.search(r'[“\x22]', para))


def extract_dialogue(text, book_title):
    """Extract attributed dialogue for all tracked characters."""
    entries = []
    seen = set()

    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\n{2,}', '\n\n', text)
    paragraphs = text.split('\n\n')

    for para in paragraphs:
        para = para.replace('\n', ' ')
        for name, patterns in DIALOGUE_PATTERNS.items():
            for pattern in patterns:
                for m in pattern.finditer(para):
                    quote = clean_text(m.group(1))
                    if MIN_LEN <= len(quote) <= MAX_LEN and quote not in seen:
                        if not should_skip(quote):
                            seen.add(quote)
                            entries.append({
                                'quote': quote,
                                'book': book_title,
                                'speaker': name,
                            })

    return entries


def extract_prose(text, book_title):
    """Extract Watson narrative prose from non-dialogue paragraphs."""
    entries = []
    seen = set()

    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\n{2,}', '\n\n', text)
    paragraphs = text.split('\n\n')

    for para in paragraphs:
        para = para.replace('\n', ' ').strip()

        # Skip paragraphs that are primarily dialogue
        if is_dialogue_para(para):
            continue

        # Skip very short paragraphs (chapter headings, labels)
        if len(para) < MIN_PROSE_LEN:
            continue

        if should_skip_prose(para):
            continue

        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', para)
        for sent in sentences:
            sent = clean_text(sent)
            if not (MIN_PROSE_LEN <= len(sent) <= MAX_PROSE_LEN):
                continue
            words = sent.split()
            if len(words) < MIN_PROSE_WORDS:
                continue
            if should_skip_prose(sent):
                continue
            if PRONOUN_START.match(sent):
                continue
            if any(n in sent.lower() for n in PROSE_PROPER_NOUNS):
                continue
            if sent not in seen:
                seen.add(sent)
                entries.append({
                    'quote': sent,
                    'book': book_title,
                    'speaker': 'narrative',
                })

    return entries


# ---------------------------------------------------------------------------
# Story segmentation
#
# The 5 short-story collections are split into their individual stories so each
# quote can be attributed to the story it came from. The 4 novels are single
# works and get story = None.
#
# Four collections carry a "Contents" list of story titles in canonical order;
# each story then begins with a standalone heading line in the body (sometimes
# ALL CAPS, sometimes prefixed with a numeral). The Case-Book has no Contents
# block, so its stories are found from their ALL-CAPS body headings directly.
# ---------------------------------------------------------------------------

NOVELS = {
    'A Study in Scarlet',
    'The Sign of the Four',
    'The Hound of the Baskervilles',
    'The Valley of Fear',
}


def _story_norm(s):
    """Whitespace/quote normalisation for substring-matching a stored quote
    (already run through clean_text) against a slice of source text."""
    s = s.replace('‘', "'").replace('’', "'")
    s = s.replace('“', '"').replace('”', '"')
    return re.sub(r'\s+', ' ', s).strip()


def _strip_numeral(s):
    s = re.sub(r'^[IVXLC]+\.\s*', '', s, flags=re.IGNORECASE)
    s = re.sub(r'^\d+\.\s*', '', s)
    return s


def _norm_title(s):
    """Normalise a heading/title for case-insensitive comparison."""
    s = _story_norm(s).lower().replace('_', '')  # drop Gutenberg italic markers
    s = _strip_numeral(s)                         # body headings keep "I."/"1." prefixes
    return re.sub(r'\s+', ' ', s.rstrip('.').strip())


def _smart_title_case(s):
    """Title-case an ALL-CAPS heading without mangling apostrophes ("LION'S" ->
    "Lion's") and keeping minor words lower except when first."""
    small = {'of', 'the', 'in', 'and', 'a', 'on', 'with', 'at', 'by', 'to', 'for'}
    words = _story_norm(s).lower().split()
    out = []
    for i, w in enumerate(words):
        if i > 0 and w in small:
            out.append(w)
        else:
            out.append(w[0].upper() + w[1:])
    return ' '.join(out)


def _parse_contents(text):
    """Return (ordered story titles, offset where the Contents block ends), or (None, 0)."""
    m = re.search(r'^Contents\s*$', text, re.MULTILINE)
    if not m:
        return None, 0
    lines = text[m.end():].split('\n')
    titles, started, blanks_after, consumed = [], False, 0, 0
    for ln in lines:
        s = ln.strip()
        if not s:
            if started:
                blanks_after += 1
                if blanks_after >= 2:   # the gap that ends the TOC
                    break
            consumed += len(ln) + 1
            continue
        blanks_after = 0
        started = True
        consumed += len(ln) + 1
        titles.append(_strip_numeral(s).rstrip('.').strip())
    return (titles or None), m.end() + consumed


def _find_headings(text, titles, start):
    """Locate each title's standalone-heading offset in the body, in order."""
    result, cursor = [], start
    line_re = re.compile(r'(?m)^[ \t]*(.+?)[ \t]*$')
    for t in titles:
        nt = _norm_title(t)
        found = None
        for lm in line_re.finditer(text, cursor):
            if lm.group(1).strip() and _norm_title(lm.group(1)) == nt:
                found = lm.start()
                break
        result.append((t, found))
        if found is not None:
            cursor = found + 1
    return result


def segment(text, book):
    """Return an ordered list of (story_title, start, end) spans over `text`.
    Empty for novels."""
    if book in NOVELS:
        return []

    body_start = 0
    sm = re.search(r'\*\*\* START OF THE PROJECT GUTENBERG.*?\*\*\*', text)
    if sm:
        body_start = sm.end()

    titles, contents_end = _parse_contents(text)

    if not titles:
        # Case-Book: detect ALL-CAPS story headings directly.
        offs, labels, cur = [], [], body_start
        head_re = re.compile(r'(?m)^(THE (?:ADVENTURE|PROBLEM) OF [A-Z][A-Z .\'’-]+)$')
        for hm in head_re.finditer(text, body_start):
            offs.append(hm.start())
            labels.append(_smart_title_case(hm.group(1)))
        spans = []
        for k, off in enumerate(offs):
            end = offs[k + 1] if k + 1 < len(offs) else len(text)
            spans.append((labels[k], off, end))
        return spans

    hs = _find_headings(text, titles, contents_end)
    spans = []
    for k, (t, off) in enumerate(hs):
        if off is None:
            continue
        nxt = next((hs[j][1] for j in range(k + 1, len(hs)) if hs[j][1] is not None), None)
        spans.append((t, off, nxt if nxt is not None else len(text)))
    return spans


def assign_stories(entries, text, book):
    """Set entry['story'] for each entry, using the story spans of `book`.
    Novels and any quote that can't be located get story = None."""
    spans = segment(text, book)
    nspans = [(t, _story_norm(text[s:e])) for (t, s, e) in spans]
    for e in entries:
        nq = _story_norm(e['quote'])
        e['story'] = next((t for (t, ns) in nspans if nq in ns), None)


def main():
    stats_only = '--stats' in sys.argv

    all_entries = []
    for title, url in BOOKS:
        print(f'Fetching: {title}...', end=' ', flush=True)
        try:
            text = fetch(url)
        except RuntimeError as e:
            print(f'FAILED ({e})')
            continue

        dialogue = extract_dialogue(text, title)
        prose = extract_prose(text, title)
        book_entries = dialogue + prose
        assign_stories(book_entries, text, title)   # sets entry['story']
        total = len(dialogue) + len(prose)
        stories = len({e['story'] for e in book_entries if e['story']})
        print(f'{total} ({len(dialogue)} dialogue, {len(prose)} prose'
              + (f', {stories} stories)' if stories else ')'))
        all_entries.extend(book_entries)

        if title != BOOKS[-1][0]:
            time.sleep(1)

    # Deduplicate across books
    seen = set()
    deduped = []
    for e in all_entries:
        if e['quote'] not in seen:
            seen.add(e['quote'])
            deduped.append(e)

    print(f'\nTotal: {len(deduped)} unique entries')

    if stats_only:
        from collections import Counter
        print('\nBy speaker:')
        by_speaker = Counter(e['speaker'] for e in deduped)
        for speaker, count in by_speaker.most_common():
            print(f'  {count:4d}  {speaker}')
        print('\nBy book:')
        by_book = Counter(e['book'] for e in deduped)
        for book, count in by_book.most_common():
            print(f'  {count:4d}  {book}')
        return

    write_output(deduped, replace='--replace' in sys.argv)


def write_output(harvested, replace=False):
    """Persist harvested entries safely.

    Default (merge): keep every entry already in the file and append only quotes
    whose text isn't already present. This preserves the existing pool and the
    posted-state that references it, so a re-harvest can never shrink the pool or
    orphan holmes_state.json. `replace=True` swaps in the fresh harvest instead.

    Either way the current file is backed up first and the new file is written
    atomically (temp file + os.replace)."""
    existing = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else []

    if replace:
        merged, kept, added = harvested, 0, len(harvested)
    else:
        have = {e['quote'] for e in existing}
        new = [e for e in harvested if e['quote'] not in have]
        merged, kept, added = existing + new, len(existing), len(new)

    if OUTPUT.exists():
        backup = OUTPUT.with_name(f'{OUTPUT.stem}.{time.strftime("%Y%m%d-%H%M%S")}.bak.json')
        shutil.copy2(OUTPUT, backup)
        print(f'Backed up existing file -> {backup.name}')
        prune_backups()

    tmp = OUTPUT.with_name(OUTPUT.name + '.tmp')
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + '\n')
    os.replace(tmp, OUTPUT)

    verb = 'Replaced' if replace else 'Merged'
    print(f'{verb}: {kept} kept + {added} new = {len(merged)} total -> {OUTPUT.name}')


def prune_backups(keep=KEEP_BACKUPS):
    """Delete all but the newest `keep` timestamped backups. The filename stamp
    (YYYYMMDD-HHMMSS) sorts chronologically, so newest = last by name."""
    backups = sorted(OUTPUT.parent.glob(f'{OUTPUT.stem}.[0-9]*.bak.json'))
    stale = backups[:-keep] if keep > 0 else backups
    for p in stale:
        p.unlink()
    if stale:
        print(f'Pruned {len(stale)} old backup(s), kept newest {min(keep, len(backups))}.')


if __name__ == '__main__':
    main()
