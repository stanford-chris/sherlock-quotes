#!/usr/bin/env python3
"""Tests for the recently-posted work memory added on 23 August 2026.

The bug being defended against: pick_quote drew uniformly over quotes, so a
work's share of the feed tracked its word count. The four novels are 51.5% of
the eligible pool but only 4 of the 61 works, and The Hound of the Baskervilles
took 12 of the first 81 posts, three of them inside nine days.

Unlike the image memory in test_pick_images.py, this memory DOES filter -- but
it narrows the window rather than dropping it, ending at zero, so it can never
starve a lane. `test_never_starves_*` are the ones to keep green.
"""
import random
import unittest

import holmes_post as h


def q(text_seed, book, story=None, speaker='narrative'):
    """A quote entry long enough to clear MIN_QUOTE_LEN and shaped to pass
    is_complete_quote, so these tests exercise selection and nothing else."""
    text = f'Quote {text_seed} padded out to clear the minimum length bound.'
    return {'quote': text, 'book': book, 'story': story, 'speaker': speaker}


class WorkOf(unittest.TestCase):

    def test_a_story_is_its_own_work(self):
        self.assertEqual(
            h.work_of(q(1, 'The Memoirs of Sherlock Holmes', 'Silver Blaze')),
            'Silver Blaze')

    def test_a_novel_falls_back_to_the_book(self):
        self.assertEqual(
            h.work_of(q(2, 'The Hound of the Baskervilles')),
            'The Hound of the Baskervilles')

    def test_two_stories_in_one_collection_are_different_works(self):
        a = h.work_of(q(3, 'The Return of Sherlock Holmes', 'The Empty House'))
        b = h.work_of(q(4, 'The Return of Sherlock Holmes', 'The Norwood Builder'))
        self.assertNotEqual(a, b)

    def test_unlike_work_key_it_never_returns_none(self):
        # _work_key gates Paget's art and returns None for a collection entry
        # with no story. A None here would collide unrelated quotes into one
        # cooldown slot, so work_of falls back to the book instead.
        self.assertIsNone(h._work_key(None, 'The Case-Book of Sherlock Holmes'))
        self.assertEqual(h.work_of(q(5, 'The Case-Book of Sherlock Holmes')),
                         'The Case-Book of Sherlock Holmes')


class Cooldown(unittest.TestCase):

    def setUp(self):
        random.seed(0)

    def test_a_recent_work_is_skipped_when_another_is_available(self):
        quotes = [q(1, 'B1', 'Hot'), q(2, 'B1', 'Cold')]
        for _ in range(50):
            picked = h.pick_quote(quotes, set(), ['Hot'])
            self.assertEqual(h.work_of(picked), 'Cold')

    def test_only_the_window_counts_not_the_whole_history(self):
        # 'Old' sits beyond a 3-wide window, so it is fair game again.
        quotes = [q(1, 'B1', 'Old')]
        recent = ['Old'] + [f'W{i}' for i in range(30)]
        picked = h.pick_quote(quotes, set(), recent[-3:])
        self.assertEqual(h.work_of(picked), 'Old')

    def test_never_starves_when_every_work_is_recent(self):
        quotes = [q(1, 'B1', 'Hot'), q(2, 'B1', 'Also')]
        picked = h.pick_quote(quotes, set(), ['Hot', 'Also'])
        self.assertIn(h.work_of(picked), {'Hot', 'Also'})

    def test_never_starves_on_a_single_remaining_work(self):
        quotes = [q(1, 'The Hound of the Baskervilles')]
        recent = ['The Hound of the Baskervilles'] * h.RECENT_WORKS_MAX
        self.assertIsNotNone(h.pick_quote(quotes, set(), recent))

    def test_the_relax_stops_at_the_widest_window_that_works(self):
        # 'Fresh' is outside the half-window but inside the full one. The full
        # window yields nothing, so the half-window is used and 'Fresh' wins
        # over 'Hot', which is recent under every width.
        recent = ['Fresh', 'x', 'y', 'z', 'Hot']
        quotes = [q(1, 'B1', 'Fresh'), q(2, 'B1', 'Hot')]
        for _ in range(50):
            picked = h.pick_quote(quotes, set(), recent)
            self.assertEqual(h.work_of(picked), 'Fresh')

    def test_an_empty_memory_changes_nothing(self):
        quotes = [q(i, 'B1', f'S{i}') for i in range(5)]
        self.assertIsNotNone(h.pick_quote(quotes, set(), []))

    def test_the_default_argument_keeps_the_old_two_argument_call_working(self):
        self.assertIsNotNone(h.pick_quote([q(1, 'B1', 'S')], set()))

    def test_posted_quotes_are_still_excluded(self):
        one, two = q(1, 'B1', 'S1'), q(2, 'B1', 'S2')
        posted = {h.quote_id(one['quote'])}
        for _ in range(20):
            self.assertEqual(h.pick_quote([one, two], posted)['quote'], two['quote'])

    def test_all_posted_still_returns_none(self):
        one = q(1, 'B1', 'S1')
        self.assertIsNone(h.pick_quote([one], {h.quote_id(one['quote'])}))

    def test_the_lane_split_survives_the_cooldown(self):
        # Dialogue is picked ~40% of the time. The lane is chosen before the
        # cooldown is applied, so holding a work off must not shift that.
        quotes = ([q(i, 'B1', f'D{i}', speaker='Holmes') for i in range(30)]
                  + [q(100 + i, 'B1', f'N{i}') for i in range(30)])
        random.seed(1)
        picks = [h.pick_quote(quotes, set(), ['D0', 'N0']) for _ in range(2000)]
        share = sum(1 for p in picks if p['speaker'] != 'narrative') / len(picks)
        self.assertAlmostEqual(share, 0.4, delta=0.05)

    def test_it_actually_spaces_a_dominant_work_out(self):
        # The shape of the real pool in miniature: one work holding half the
        # quotes, against ten small ones. Without the memory the big work takes
        # about half the feed; with it, no more than its turn in the rotation.
        big = [q(i, 'Big') for i in range(50)]
        small = [q(1000 + i, 'B1', f'S{i}') for i in range(10)]
        random.seed(2)
        recent, runs = [], []
        for _ in range(60):
            picked = h.pick_quote(big + small, set(), recent)
            w = h.work_of(picked)
            runs.append(w)
            recent = ([x for x in recent if x != w] + [w])[-h.RECENT_WORKS_MAX:]
        self.assertLess(runs.count('Big') / len(runs), 0.2)
        at = [i for i, w in enumerate(runs) if w == 'Big']
        gaps = [b - a for a, b in zip(at, at[1:])]
        self.assertTrue(gaps and all(g >= 2 for g in gaps), runs)


class RecentListMaintenance(unittest.TestCase):
    """The append rule in main(), asserted directly: a work posted again must
    move to the newest end, not keep its old slot at the stale end."""

    @staticmethod
    def append(recent, used):
        return ([w for w in recent if w != used] + [used])[-h.RECENT_WORKS_MAX:]

    def test_reuse_moves_the_work_to_the_newest_end(self):
        self.assertEqual(self.append(['a', 'b', 'c'], 'a'), ['b', 'c', 'a'])

    def test_no_duplicate_ever_occupies_two_slots(self):
        recent = []
        for used in ['a', 'b', 'a', 'c', 'a']:
            recent = self.append(recent, used)
        self.assertEqual(recent, ['b', 'c', 'a'])

    def test_the_list_is_capped(self):
        recent = []
        for i in range(h.RECENT_WORKS_MAX + 25):
            recent = self.append(recent, f'w{i}')
        self.assertEqual(len(recent), h.RECENT_WORKS_MAX)
        self.assertEqual(recent[-1], f'w{h.RECENT_WORKS_MAX + 24}')

    def test_the_window_leaves_most_of_the_canon_drawable(self):
        # 61 works in the eligible pool. A window approaching that would make
        # the feed a predictable cycle through the canon rather than a draw.
        self.assertLess(h.RECENT_WORKS_MAX, 61 / 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
