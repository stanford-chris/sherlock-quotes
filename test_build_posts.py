#!/usr/bin/env python3
"""Tests for where the hashtags land.

⛔ The point of this file is one fact that is invisible from inside the bot:
hashtag feed generators index TOP-LEVEL posts only. Checked 23 August 2026
against the #History feed (100 items) and the largest Photography feed (69) --
not one item in either was a reply. So a tag on post 2 of a thread reaches
nobody, and for the 18 percent of posts that thread, the bot was posting into
the void with no error, no log line and a feed that looked entirely normal.

Hermetic: no network, no state file, no post. Stdlib plus atproto, which
holmes_post imports anyway.
"""

import json
import unittest
from pathlib import Path

import holmes_post as h

IMAGE = {'credit_name': 'Library of Congress', 'source': 'loc',
         'page_url': 'https://www.loc.gov/item/example/', 'date': '1895'}
QUOTE = 'You see, but you do not observe. The distinction is clear.'


def facet_tags(tb):
    """Every hashtag carried as a real facet, not merely typed into the text."""
    tags = []
    for facet in tb.build_facets():
        for feature in facet.features:
            tag = getattr(feature, 'tag', None)
            if tag:
                tags.append(tag)
    return tags


class WhereTheTagsLand(unittest.TestCase):

    def test_post1_carries_the_tags(self):
        """The root post of a thread. This is the whole fix."""
        tb = h.build_post1(QUOTE)
        self.assertIn('#SherlockHolmes', tb.build_text())

    def test_post1_tags_are_facets_not_just_text(self):
        """A hashtag typed into the text is not indexed the same way as a
        facet, and the difference is invisible in the rendered post."""
        self.assertEqual(facet_tags(h.build_post1(QUOTE)),
                         [t for _, t in h.TAGS])

    def test_post2_does_not_repeat_them(self):
        """The reply gains nothing from a tag and would show the same hashtag
        twice in two consecutive posts."""
        tb = h.build_post2('Sherlock Holmes', 'A Study in Scarlet', None, IMAGE)
        self.assertNotIn('#', tb.build_text())
        self.assertEqual(facet_tags(tb), [])

    def test_the_single_post_still_carries_them(self):
        """The common path, which threads only on an over-length quote: this
        must not have been broken by moving the tags off build_post2."""
        tb = h.build_combined(QUOTE, 'Sherlock Holmes', 'A Study in Scarlet',
                              None, IMAGE)
        self.assertIn('#SherlockHolmes', tb.build_text())
        self.assertEqual(facet_tags(tb), [t for _, t in h.TAGS])

    def test_the_attribution_still_comes_before_the_tags(self):
        """Order matters: the tags sit under the credit, not between the quote
        and its attribution."""
        text = h.build_combined(QUOTE, 'Sherlock Holmes', 'A Study in Scarlet',
                                None, IMAGE).build_text()
        self.assertLess(text.index('Library of Congress'), text.index('#'))


class HowATitleIsNamed(unittest.TestCase):
    """Every work title is quoted, novel and short story alike.

    ⛔ The failure this pins is silent. Until 26 August 2026 only a collection
    story was quoted and the four novels went out bare -- print convention, but
    Bluesky has no italics, so a novel simply lost its marking and read as loose
    text. Nothing asserted either behaviour, no comment explained it, and the
    quote marks looked like a side effect of the two link branches rather than a
    decision. A regression here produces a perfectly plausible post.
    """

    def _title_line(self, book, story):
        tb = h.append_attribution(h.CurlyTextBuilder(), None, book, story, IMAGE)
        return tb.build_text().split('\n')[0]

    def test_a_novel_is_quoted(self):
        for novel in h.NOVELS:
            with self.subTest(novel=novel):
                self.assertIn(f'\u201c{novel}\u201d', self._title_line(novel, None))

    def test_a_collection_story_is_quoted(self):
        line = self._title_line('The Return of Sherlock Holmes',
                                'The Adventure of the Dancing Men')
        self.assertIn('\u201cThe Adventure of the Dancing Men\u201d', line)

    def test_a_quote_located_no_finer_than_its_collection_is_quoted(self):
        line = self._title_line('The Return of Sherlock Holmes', None)
        self.assertIn('\u201cThe Return of Sherlock Holmes\u201d', line)

    def test_no_title_goes_out_bare(self):
        """The shape of the old bug: a title sitting in the line unquoted."""
        for book, story in [('The Valley of Fear', None),
                            ('The Return of Sherlock Holmes', 'Silver Blaze')]:
            with self.subTest(book=book, story=story):
                line = self._title_line(book, story)
                self.assertEqual(line.count('\u201c'), 1, line)
                self.assertEqual(line.count('\u201d'), 1, line)

    def test_the_link_covers_the_title_and_not_the_quote_marks(self):
        """A curly quote inside the facet would put it in the link text."""
        tb = h.append_attribution(h.CurlyTextBuilder(), None,
                                  'The Sign of the Four', None, IMAGE)
        raw = tb.build_text().encode('utf-8')
        links = [f for f in tb.build_facets()
                 if any(getattr(x, 'uri', '').startswith('https://www.gutenberg.org')
                        for x in f.features)]
        self.assertEqual(len(links), 1)
        i = links[0].index
        self.assertEqual(raw[i.byte_start:i.byte_end].decode('utf-8'),
                         'The Sign of the Four')


class Length(unittest.TestCase):

    def test_the_longest_quote_plus_the_tag_line_still_fits(self):
        """⚠️ The headroom is thin. Post 1 was quote-only and had the whole
        290-character budget; it now spends 17 of it on the tag line. The
        longest quote in the pool renders at 262, leaving 11 to spare, so a
        re-harvest that lengthens the pool would silently overrun."""
        pool = h.QUOTES_FILE
        if not Path(pool).exists():
            self.skipTest(f'{pool} not on this machine')
        quotes = json.loads(Path(pool).read_text())
        longest = max(quotes, key=lambda q: len(q['quote']))['quote']
        built = len(h.build_post1(longest).build_text())
        self.assertLessEqual(
            built, h.MAX_CHARS,
            f'longest quote builds to {built} against MAX_CHARS {h.MAX_CHARS}')


if __name__ == '__main__':
    unittest.main()
