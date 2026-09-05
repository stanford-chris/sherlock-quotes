#!/usr/bin/env python3
"""Tests for the manuscript-title filter added 5 September 2026.

The bug being defended against: NOT_A_PHOTOGRAPH excludes non-photographic
media by LOC subject heading, but a manuscript page reproduced as a museum
postcard is catalogued 'postcards'/'galleries & museums', not 'manuscripts',
so it slipped through and posted under a camera credit. The Harley MS. 7368
Sir Thomas More page did exactly this on 5 Sep 2026, and its alt text fell
back to the bare LOC caption because image_alt.describe() correctly refused
to guess at the handwriting.

The false-positive test is the point: the pattern must not start excluding
the hundreds of legitimate London postcard photographs the pool also holds.
"""
import json
import tempfile
import unittest
from pathlib import Path

import holmes_post as h


def loc_item(title, subjects=('postcards', 'england', 'london')):
    return {
        'id': 'http://www.loc.gov/item/x/', 'title': title, 'date': '1900',
        'subjects': list(subjects), 'tags': [],
        'image_url': 'https://example.com/x.jpg',
        'thumb_url': 'https://example.com/x_150px.jpg',
    }


def load(items):
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(items, f)
        path = Path(f.name)
    try:
        return h.load_photos(path)
    finally:
        path.unlink()


class ManuscriptTitleFilter(unittest.TestCase):
    def test_excludes_the_reported_sir_thomas_more_page(self):
        item = loc_item(
            "Twelve lines of the play 'Sir Thomas More', written about 1594 "
            "(?), being part of a passage (substituted on revision which "
            "there are strong grounds for believing to be the work, and in "
            "the handwriting, of William Shakespeare). [Harley MS. 7368, f. 9")
        self.assertEqual(load([item]), [])

    def test_excludes_other_manuscript_reproductions_not_tagged_manuscripts(self):
        items = [
            loc_item("Entries in Milton's Family Bible, the earlier ones in "
                     "his own hand. [Add. MS. 32310"),
            loc_item("Bible in English, the earlier Wycliffite version, end "
                     "of 14th century [Egerton MS. 618]."),
        ]
        self.assertEqual(load(items), [])

    def test_a_bare_ms_still_needs_a_word_boundary(self):
        # Regression guard on the regex shape, not a real LOC title.
        self.assertIsNone(h._MANUSCRIPT_TITLE.search('MSNBC building, London'))

    def test_does_not_exclude_an_ordinary_london_postcard(self):
        item = loc_item('Tower Bridge, London')
        self.assertEqual(len(load([item])), 1)

    def test_does_not_exclude_a_museum_building_postcard(self):
        # Same subjects as the manuscript pages above; only the title differs.
        item = loc_item('British Museum, London',
                         subjects=('postcards', 'galleries & museums', 'london'))
        self.assertEqual(len(load([item])), 1)


if __name__ == '__main__':
    unittest.main()
