#!/usr/bin/env python3
"""Tests for upload_image_blob() in holmes_post.py.

Added 6 September 2026 after the 21:00 KST post was missed: send_images()'s
own internal upload_blob() call timed out before any post existed to create.
login_client() already retries a transient network blip at login; this is the
same fix for the upload step, which is verified here as SAFE to retry for a
different reason than login is safe -- uploading a blob creates no visible
post, only a stored blob reference.

The failure to fear is not "does it retry" but "does it ever retry the wrong
thing": the whole point of leaving send_post() unretried (see
login_client()'s docstring) is that a timeout there cannot tell a post that
never landed from one that landed with the response lost, and this fix must
not blur that line. So the tests below cover the retry mechanics only, on
upload_blob() alone -- nothing here calls send_post().
"""
import time
import unittest
from unittest import mock

from atproto import exceptions

import holmes_post as hp


class FakeClient:
    """A client whose upload_blob() fails a fixed number of times, then
    either succeeds or keeps failing -- never anything holmes_post.py cannot
    already tell apart from a real atproto client for this one call."""

    def __init__(self, fail_times, error_cls=exceptions.InvokeTimeoutError):
        self.fail_times = fail_times
        self.error_cls = error_cls
        self.calls = 0

    def upload_blob(self, image_bytes):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error_cls(f'timed out (call {self.calls})')
        return f'blob-for-{image_bytes!r}'


class UploadImageBlob(unittest.TestCase):
    def setUp(self):
        # Never actually sleep in a test: the backoff itself is not what is
        # under test, only that it happens between attempts, not before the
        # first or after the last.
        self.sleep_patcher = mock.patch.object(hp.time, 'sleep')
        self.mock_sleep = self.sleep_patcher.start()

    def tearDown(self):
        self.sleep_patcher.stop()

    def test_a_clean_first_attempt_needs_no_retry_and_no_sleep(self):
        client = FakeClient(fail_times=0)
        result = hp.upload_image_blob(client, b'abc')
        self.assertEqual(result, "blob-for-b'abc'")
        self.assertEqual(client.calls, 1)
        self.mock_sleep.assert_not_called()

    def test_a_transient_blip_recovers_within_the_retry_budget(self):
        client = FakeClient(fail_times=2)
        result = hp.upload_image_blob(client, b'abc')
        self.assertEqual(result, "blob-for-b'abc'")
        self.assertEqual(client.calls, 3)
        self.assertEqual(self.mock_sleep.call_count, 2)  # between attempts only

    def test_exhausting_every_attempt_raises_a_named_runtime_error(self):
        client = FakeClient(fail_times=99)  # never recovers
        with self.assertRaises(RuntimeError) as ctx:
            hp.upload_image_blob(client, b'abc', retries=4)
        self.assertEqual(client.calls, 4)
        self.assertIn('after 4 attempts', str(ctx.exception))
        self.assertIn('InvokeTimeoutError', str(ctx.exception))

    def test_the_last_attempt_does_not_sleep_afterward(self):
        # A sleep after the final attempt would just slow down the eventual
        # failure for no benefit -- there is nothing left to retry.
        client = FakeClient(fail_times=99)
        with self.assertRaises(RuntimeError):
            hp.upload_image_blob(client, b'abc', retries=3)
        self.assertEqual(self.mock_sleep.call_count, 2)  # attempts 1,2 sleep; 3 does not

    def test_a_non_network_error_is_not_caught_and_not_retried(self):
        # upload_image_blob() must not become a general-purpose retry-anything
        # wrapper: only atproto's own NetworkError family is transient-network
        # shaped. Anything else (a bad image, a bug) should surface at once.
        class Broken:
            def upload_blob(self, image_bytes):
                raise ValueError('not a network problem')

        with self.assertRaises(ValueError):
            hp.upload_image_blob(Broken(), b'abc')
        self.mock_sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
