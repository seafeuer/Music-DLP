"""Tests for link classification and filename handling."""

import unittest
import sys
from pathlib import Path

# Allow running from the repository root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from downloaders.url_utils import classify_url, split_links, single_collection_id
from gui.workers.conversion_worker import ConversionWorker


class ClassifyUrlTests(unittest.TestCase):
    def test_youtube_playlist(self):
        info = classify_url('https://www.youtube.com/playlist?list=PLabc123')
        self.assertEqual(info.platform, 'youtube')
        self.assertEqual(info.kind, 'playlist')
        self.assertEqual(info.media_id, 'PLabc123')

    def test_youtube_video(self):
        info = classify_url('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(info.platform, 'youtube')
        self.assertEqual(info.kind, 'video')
        self.assertEqual(info.media_id, 'dQw4w9WgXcQ')

    def test_watch_url_with_playlist_is_a_video(self):
        info = classify_url(
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123'
        )
        self.assertEqual(info.kind, 'video')
        self.assertEqual(info.media_id, 'dQw4w9WgXcQ')

    def test_youtu_be_short_link(self):
        info = classify_url('https://youtu.be/dQw4w9WgXcQ?t=10')
        self.assertEqual(info.kind, 'video')
        self.assertEqual(info.media_id, 'dQw4w9WgXcQ')

    def test_youtube_shorts(self):
        info = classify_url('https://youtube.com/shorts/dQw4w9WgXcQ')
        self.assertEqual(info.kind, 'video')
        self.assertEqual(info.media_id, 'dQw4w9WgXcQ')

    def test_schemeless_link(self):
        info = classify_url('youtu.be/dQw4w9WgXcQ')
        self.assertEqual(info.url, 'https://youtu.be/dQw4w9WgXcQ')

    def test_x_status(self):
        info = classify_url('https://x.com/someuser/status/1234567890')
        self.assertEqual(info.platform, 'twitter')
        self.assertEqual(info.kind, 'video')
        self.assertEqual(info.media_id, '1234567890')

    def test_twitter_status_with_photo_suffix(self):
        info = classify_url('https://twitter.com/someuser/status/123/photo/1')
        self.assertEqual(info.platform, 'twitter')
        self.assertEqual(info.media_id, '123')

    def test_twitter_proxy_is_rewritten(self):
        info = classify_url('https://vxtwitter.com/someuser/status/123')
        self.assertEqual(info.platform, 'twitter')
        self.assertEqual(info.media_id, '123')
        self.assertTrue(info.url.startswith('https://x.com/'))

    def test_twitter_profile_is_not_supported(self):
        self.assertIsNone(classify_url('https://x.com/someuser'))

    def test_other_site_is_accepted(self):
        info = classify_url('https://vimeo.com/12345')
        self.assertEqual(info.platform, 'other')
        self.assertEqual(info.kind, 'video')

    def test_non_url_is_rejected(self):
        self.assertIsNone(classify_url('not a link'))
        self.assertIsNone(classify_url(''))


class SplitLinksTests(unittest.TestCase):
    def test_splits_lines_and_dedupes(self):
        text = (
            'https://youtu.be/dQw4w9WgXcQ\n'
            'https://x.com/user/status/123\n'
            'https://youtu.be/dQw4w9WgXcQ\n'
        )
        self.assertEqual(
            split_links(text),
            ['https://youtu.be/dQw4w9WgXcQ', 'https://x.com/user/status/123']
        )

    def test_ignores_surrounding_text(self):
        text = 'watch this https://youtu.be/dQw4w9WgXcQ please'
        self.assertEqual(split_links(text), ['https://youtu.be/dQw4w9WgXcQ'])

    def test_empty(self):
        self.assertEqual(split_links(''), [])

    def test_collection_id_is_filesystem_safe(self):
        collection_id = single_collection_id('twitter', '123/456')
        self.assertEqual(collection_id, 'single_twitter_123_456')


class SafeFilenameTests(unittest.TestCase):
    def test_keeps_unicode(self):
        self.assertEqual(ConversionWorker._safe_filename('夜に駆ける'), '夜に駆ける')

    def test_replaces_invalid_characters(self):
        self.assertEqual(
            ConversionWorker._safe_filename('AC/DC: Back in Black?'),
            'AC_DC_ Back in Black_'
        )

    def test_collapses_newlines(self):
        self.assertEqual(
            ConversionWorker._safe_filename('Line one\nLine two'),
            'Line one Line two'
        )

    def test_empty_falls_back(self):
        self.assertEqual(ConversionWorker._safe_filename('   '), 'Untitled')


if __name__ == '__main__':
    unittest.main()
