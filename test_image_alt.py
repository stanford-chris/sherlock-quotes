"""Tests for the operator-aside strip in image_alt.

Run from this directory:

    python3 -m unittest              # or: python3 test_image_alt.py

Stdlib only, and no network or model call: `_strip_meta` is a pure string
function, so the cases below are the real ones written out verbatim.

The incident behind them happened to the sibling Old Seoul bot, which runs
this same module: on 16 August 2026 the model answered the operator instead
of the reader, and a screen reader read the aside aloud before reaching the
description. This bot has logged no such leak, but its alt log is days old,
so absence proves very little and the exposure is identical.

The first guard could drop only two sentences from each end, and that aside
was exactly two. A third sentence of preamble would have shipped, which is
why the front of the strip is now unbounded.

The last three matter most. An over-eager strip is the worse bug of the two,
because it would quietly delete real description from live posts, and unlike
a leaked aside nobody would ever see what was lost.
"""

import unittest

import image_alt

DESC = ('Pen-and-ink illustration of a man in a long coat gesturing across a '
        'desk towards a seated figure, with a mantelpiece clock behind.')

# The Old Seoul aside, verbatim, with this bot's kind of description after it.
SHIPPED = ("Note: this image doesn't match the caption, it shows a rooftop "
           'pigeon coop, not a city plaza. Flagging that before giving alt '
           'text. ' + DESC)


class StripsOperatorAsides(unittest.TestCase):

    def strip(self, text):
        return image_alt._strip_meta(text, log=lambda *_: None).strip()

    def test_the_aside_that_actually_shipped(self):
        self.assertEqual(self.strip(SHIPPED), DESC)

    def test_curly_apostrophe(self):
        # A model emits "doesn’t" more often than "doesn't", and the pattern
        # for the contradiction remark only spells the straight form.
        self.assertEqual(self.strip(SHIPPED.replace("doesn't", 'doesn’t')), DESC)

    def test_contradiction_without_a_note_opener(self):
        # No "Note:" and no "alt text": the one case where the caption rule
        # has to catch it alone.
        self.assertEqual(
            self.strip('This image doesn’t match the caption. ' + DESC), DESC)

    def test_lead_in_ending_in_a_colon(self):
        # No sentence break after the colon, so sentence-level stripping
        # cannot see it. Handled by _LEAD_IN before the split.
        self.assertEqual(self.strip("Here's the alt text: " + DESC), DESC)

    def test_three_sentences_of_preamble(self):
        # The regression the two-sentence bound allowed: the third survived.
        self.assertEqual(self.strip(
            'Note: the caption is wrong. Flagging that before giving alt '
            'text. I should mention the mismatch. ' + DESC), DESC)

    def test_five_sentences_of_preamble(self):
        self.assertEqual(self.strip(
            'Sure, I can help with that. Note: the caption is wrong. '
            'Flagging that before giving alt text. I should mention the '
            'mismatch. Here is my alt text attempt. ' + DESC), DESC)

    def test_trailing_sign_off(self):
        self.assertEqual(
            self.strip(DESC + ' Let me know if you want it shorter.'), DESC)


class LeavesRealDescriptionsAlone(unittest.TestCase):
    """The failure that would never be noticed, so it is tested hardest."""

    def strip(self, text):
        return image_alt._strip_meta(text, log=lambda *_: None).strip()

    def test_one_sentence_is_untouched(self):
        self.assertEqual(self.strip(DESC), DESC)

    def test_two_sentences_are_untouched(self):
        text = ('Pen-and-ink illustration of two men in a study. The taller '
                'figure gestures towards a desk.')
        self.assertEqual(self.strip(text), text)

    def test_a_reply_that_is_meta_throughout_returns_nothing(self):
        # Not a salvaged fragment: too short for describe() to accept, so the
        # caller falls back to the attribution, which is the right outcome.
        out = self.strip('Note: the caption is wrong. Flagging that before '
                         'giving alt text. Let me know if you want another.')
        self.assertLess(len(out), image_alt.MIN_CHARS)


if __name__ == '__main__':
    unittest.main()
