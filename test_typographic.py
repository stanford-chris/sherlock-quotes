"""Curly quotes in what the bot actually ships.

Until 23 August 2026 the only curling here was inside format_quote, so it
covered the quote body and nothing else: a story title went out as "The
Adventure of the Lion's Mane", and alt text -- the LoC catalogue title, the
story name and the model's own description alike -- was never curled at all.

Two things below are the point, rather than the conversion itself:

  * the facet test. Curling the ASSEMBLED post is the tempting version and it
    is silently wrong: a straight apostrophe is one byte where a curly one is
    three, so every link slides off the words it belongs to. Nothing about the
    text looks wrong afterwards, which is why this is pinned.

  * the elision cases. everylibrary's typographic(), which this was adapted
    from, has no digit guard because its corpus holds no elisions. The Canon
    holds five opening-position apostrophes and they split both ways.
"""
import unittest

import holmes_post as H


class Elisions(unittest.TestCase):
    """The five opening-position apostrophes in the quote pool, in context."""

    def test_elided_year_closes(self):
        self.assertEqual(H.typographic("it was in June, '89—there"),
                         'it was in June, ’89—there')
        self.assertEqual(H.typographic("In June of '95, only one"),
                         'In June of ’95, only one')

    def test_elided_decade_and_its_possessive(self):
        self.assertEqual(H.typographic("the end of the '80's, when"),
                         'the end of the ’80’s, when')

    def test_nested_quotation_opens(self):
        self.assertEqual(H.typographic("neighbour, 'Look at the steps"),
                         'neighbour, ‘Look at the steps')
        self.assertEqual(H.typographic("said 'replaced it there,'"),
                         'said ‘replaced it there,’')


class Apostrophes(unittest.TestCase):
    def test_possessive(self):
        self.assertEqual(H.typographic("The Adventure of the Lion's Mane"),
                         'The Adventure of the Lion’s Mane')

    def test_spaced_possessive_does_not_open(self):
        # everylibrary's guard: "Jefferson ‘s" is worse than the straight
        # apostrophe this whole change exists to fix.
        self.assertEqual(H.typographic("Thomas Jefferson 's Monticello"),
                         'Thomas Jefferson ’s Monticello')

    def test_double_quotes_pair(self):
        # The Whistler alt text, as it shipped on 22 August 2026.
        self.assertEqual(
            H.typographic('probably "The Vale", 1903'),
            'probably “The Vale”, 1903')

    def test_idempotent(self):
        once = H.typographic("the '80's and 'Look")
        self.assertEqual(H.typographic(once), once)

    def test_empty_and_none(self):
        self.assertEqual(H.typographic(''), '')
        self.assertEqual(H.typographic(None), '')


class FormatQuote(unittest.TestCase):
    def test_curls_before_wrapping(self):
        # A quote opening on a nested quotation would otherwise see the
        # wrapping left-double as its preceding character and close instead.
        self.assertEqual(H.format_quote("'Look at the steps"),
                         '“‘Look at the steps”')


class Facets(unittest.TestCase):
    """Curling must not slide a link off the words it belongs to."""

    def _built(self):
        return H.build_combined(
            "He said 'Look' in June of '95, at Watson's side.",
            'Holmes', 'The Case-Book of Sherlock Holmes',
            "The Adventure of the Lion's Mane",
            {'source': 'strand',
             'credit_name': 'Sidney Paget, The Strand Magazine',
             # A LITERAL apostrophe, not %27: the harvester's page_url
             # fallback is '.../wiki/' + title.replace(' ', '_'), so an
             # unencoded one is reachable. With %27 here this test passes
             # even when the URL IS curled, which is no test at all.
             'page_url': "https://commons.wikimedia.org/wiki/File:Queen's_P.jpg"})

    def test_link_byte_range_still_covers_its_own_text(self):
        tb = self._built()
        raw = tb.build_text().encode('utf-8')
        spans = [raw[f.index.byte_start:f.index.byte_end].decode('utf-8')
                 for f in tb.build_facets()]
        self.assertIn('The Adventure of the Lion’s Mane', spans)
        self.assertIn('Sidney Paget, The Strand Magazine', spans)

    def test_url_is_never_curled(self):
        # A curled apostrophe in a path is a 404, so the URL passes through
        # exactly as stored while its display text is converted.
        uris = [feat.uri for f in self._built().build_facets()
                for feat in f.features if hasattr(feat, 'uri')]
        self.assertIn("https://commons.wikimedia.org/wiki/File:Queen's_P.jpg",
                      uris)

    def test_no_straight_quotes_survive_in_the_post(self):
        t = self._built().build_text()
        self.assertNotIn("'", t)
        self.assertNotIn('"', t)

    def test_hashtag_is_not_curled(self):
        tags = [feat.tag for f in self._built().build_facets()
                for feat in f.features if hasattr(feat, 'tag')]
        self.assertTrue(tags)
        for tag in tags:
            self.assertNotIn('’', tag)


if __name__ == '__main__':
    unittest.main()
