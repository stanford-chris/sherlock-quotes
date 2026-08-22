#!/usr/bin/env python3
"""Tests for the recently-used image memory added on 22 August 2026.

The bug being defended against: holmes_post.py kept no record of which art it
had posted, so every draw was independent. On 21 and 22 August the same
Library of Congress photograph went out twice in three consecutive posts.

⚠️ The point of these tests is that the memory REORDERS and never FILTERS.
Two of the sixteen Paget works hold exactly one illustration, so a hard
exclusion would leave those stories with no art at all -- a fix that turns a
cosmetic repeat into a failed post. `test_never_starves_*` are the ones to
keep green.
"""
import random
import unittest

import holmes_post as h


def photo(item_id, title='t'):
    return {'source': 'loc', 'page_url': item_id, 'image_url': item_id + '.jpg',
            'title': title, 'story': None, 'book': None}


def scene(url, story, book='The Adventures of Sherlock Holmes'):
    return {'source': 'strand', 'image_url': url, 'page_url': url,
            'story': story, 'book': book, 'title': story}


class ImageId(unittest.TestCase):
    def test_prefers_the_permanent_page_url(self):
        self.assertEqual(h.image_id(photo('http://loc.gov/item/1/')),
                         'http://loc.gov/item/1/')

    def test_falls_back_to_image_url_then_title(self):
        self.assertEqual(h.image_id({'image_url': 'u.jpg', 'title': 'T'}), 'u.jpg')
        self.assertEqual(h.image_id({'title': 'T'}), 'T')

    def test_never_returns_none(self):
        # A None id would collide with every other missing id in the recent
        # list, quietly benching unrelated images.
        self.assertEqual(h.image_id({}), '')


class Freshest(unittest.TestCase):
    def test_unused_images_come_before_used_ones(self):
        pool = [photo('a'), photo('b'), photo('c')]
        out = h._freshest(pool, ['a', 'b'])
        self.assertEqual(h.image_id(out[0]), 'c')

    def test_used_images_are_ordered_least_recently_used_first(self):
        pool = [photo('a'), photo('b'), photo('c')]
        # recent is oldest-first, so 'a' has been unseen the longest.
        out = h._freshest(pool, ['a', 'b', 'c'])
        self.assertEqual([h.image_id(p) for p in out], ['a', 'b', 'c'])

    def test_returns_every_image_it_was_given(self):
        pool = [photo(str(i)) for i in range(20)]
        out = h._freshest(pool, ['3', '7'])
        self.assertCountEqual([h.image_id(p) for p in out],
                              [h.image_id(p) for p in pool])

    def test_never_starves_a_pool_that_is_entirely_recent(self):
        pool = [photo('a')]
        self.assertEqual(len(h._freshest(pool, ['a'])), 1)

    def test_never_starves_an_empty_recent_list(self):
        pool = [photo('a'), photo('b')]
        self.assertEqual(len(h._freshest(pool, [])), 2)

    def test_fresh_images_are_shuffled_not_returned_in_pool_order(self):
        pool = [photo(str(i)) for i in range(40)]
        random.seed(1)
        first = [h.image_id(p) for p in h._freshest(pool, [])]
        self.assertNotEqual(first, [h.image_id(p) for p in pool])


class PickImages(unittest.TestCase):
    def test_photo_lane_leads_with_art_not_posted_before(self):
        photos = [photo('a'), photo('b'), photo('c')]
        quote = {'quote': 'A quote.', 'story': 'Unillustrated Story',
                 'book': 'His Last Bow'}
        got = h.pick_images([], photos, quote, n=3, recent=['a', 'b'])
        self.assertEqual(h.image_id(got[0]), 'c')

    def test_the_repeat_that_prompted_this_cannot_happen_again(self):
        # 21 Aug 09:00 and 22 Aug 09:00 both shipped this photograph.
        royal = 'http://www.loc.gov/item/2025704735/'
        photos = [photo(royal)] + [photo(f'p{i}') for i in range(50)]
        quote = {'quote': 'A quote.', 'story': None, 'book': 'His Last Bow'}
        for _ in range(200):
            got = h.pick_images([], photos, quote, n=6, recent=[royal])
            self.assertNotEqual(h.image_id(got[0]), royal)

    def test_paget_lane_prefers_an_unused_illustration_of_the_same_story(self):
        scenes = [scene('s1', 'The Naval Treaty'), scene('s2', 'The Naval Treaty')]
        quote = {'quote': 'A quote.', 'story': 'The Naval Treaty',
                 'book': 'The Memoirs of Sherlock Holmes'}
        got = h.pick_images(scenes, [], quote, n=2, recent=['s1'])
        self.assertEqual(h.image_id(got[0]), 's2')

    def test_never_starves_a_story_with_one_illustration(self):
        # Beryl Coronet and Abbey Grange have exactly one Paget scene each.
        scenes = [scene('only', 'The Adventure of the Beryl Coronet')]
        quote = {'quote': 'A quote.', 'story': 'The Adventure of the Beryl Coronet',
                 'book': 'The Adventures of Sherlock Holmes'}
        got = h.pick_images(scenes, [photo('x')], quote, n=6, recent=['only'])
        self.assertTrue(got, 'a one-illustration story must still get its art')
        self.assertEqual(h.image_id(got[0]), 'only')

    def test_never_starves_a_photo_pool_that_is_entirely_recent(self):
        photos = [photo('a'), photo('b')]
        quote = {'quote': 'A quote.', 'story': None, 'book': 'His Last Bow'}
        got = h.pick_images([], photos, quote, n=6, recent=['a', 'b'])
        self.assertEqual(len(got), 2)


class RecentListMaintenance(unittest.TestCase):
    """The append rule in main(), asserted directly: an image used again must
    move to the newest end, not keep its old slot at the stale end."""

    @staticmethod
    def append(recent, used):
        return ([i for i in recent if i != used] + [used])[-h.RECENT_IMAGES_MAX:]

    def test_reuse_moves_the_image_to_the_newest_end(self):
        self.assertEqual(self.append(['a', 'b', 'c'], 'a'), ['b', 'c', 'a'])

    def test_no_duplicate_ever_occupies_two_slots(self):
        recent = []
        for used in ['a', 'b', 'a', 'c', 'a']:
            recent = self.append(recent, used)
        self.assertEqual(recent, ['b', 'c', 'a'])

    def test_the_list_is_capped(self):
        recent = []
        for i in range(h.RECENT_IMAGES_MAX + 25):
            recent = self.append(recent, f'i{i}')
        self.assertEqual(len(recent), h.RECENT_IMAGES_MAX)
        self.assertEqual(recent[-1], f'i{h.RECENT_IMAGES_MAX + 24}')

    def test_the_cap_leaves_most_of_the_photo_pool_fresh(self):
        # 657 usable photographs as at 18 Aug 2026. A cap that approached the
        # pool size would push every draw into the least-recently-used tail
        # and make the feed cycle predictably.
        self.assertLess(h.RECENT_IMAGES_MAX, 657 / 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
