"""Integration tests for storing extracted metadata in the database."""

import logging
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.database import Database
from downloaders.metadata_extractor import MetadataExtractor


class ExtractionFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / 'test.db'))
        self.extractor = MetadataExtractor(
            database=self.db,
            logger=logging.getLogger('test_extractor'),
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_single_twitter_post_becomes_one_item_collection(self):
        info = {
            'id': '1234567890',
            'title': 'A tweet with a song',
            'uploader': 'Some Artist',
            'duration': 42,
            'webpage_url': 'https://x.com/user/status/1234567890',
            'description': 'tweet text',
            'thumbnail': 'https://pbs.twimg.com/thumb.jpg',
        }

        success, error, data = self.extractor._store_single_info(
            info, 'https://x.com/user/status/1234567890', store_in_db=True
        )

        self.assertTrue(success, error)
        self.assertEqual(data['playlist_id'], 'single_twitter_1234567890')
        self.assertTrue(data['is_single'])
        self.assertEqual(data['video_count'], 1)

        collection = self.db.get_playlist('single_twitter_1234567890')
        self.assertIsNotNone(collection)
        self.assertEqual(collection['title'], 'A tweet with a song')

        videos = self.db.get_playlist_videos('single_twitter_1234567890')
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]['title'], 'A tweet with a song')
        self.assertEqual(videos[0]['uploader'], 'Some Artist')
        self.assertEqual(videos[0]['duration'], 42)
        # No YouTube-style thumbnail guess for a non-YouTube link
        self.assertEqual(videos[0]['thumbnail_url'], 'https://pbs.twimg.com/thumb.jpg')

    def test_youtube_video_gets_thumbnail_fallback(self):
        info = {
            'id': 'dQw4w9WgXcQ',
            'title': 'A video',
            'uploader': 'Channel',
            'duration': 100,
            'webpage_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        }

        success, error, data = self.extractor._store_single_info(
            info, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', store_in_db=True
        )

        self.assertTrue(success, error)
        videos = self.db.get_playlist_videos(data['playlist_id'])
        self.assertEqual(
            videos[0]['thumbnail_url'],
            'https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg'
        )

    def test_playlist_keeps_order_and_count(self):
        info = {
            '_type': 'playlist',
            'id': 'PLtest',
            'title': 'Test playlist',
            'uploader': 'Uploader',
            'entries': [
                {'id': 'aaa', 'title': 'First', 'url': 'https://youtu.be/aaa'},
                None,
                {'id': 'bbb', 'title': 'Second', 'url': 'https://youtu.be/bbb'},
            ],
        }

        success, error, data = self.extractor._store_playlist_info(
            info, 'https://www.youtube.com/playlist?list=PLtest', store_in_db=True
        )

        self.assertTrue(success, error)
        self.assertFalse(data['is_single'])
        self.assertEqual(data['video_count'], 3)  # includes the null entry
        self.assertEqual(len(data['videos']), 2)

        videos = self.db.get_playlist_videos('PLtest')
        self.assertEqual([v['video_id'] for v in videos], ['aaa', 'bbb'])
        self.assertEqual([v['position'] for v in videos], [1, 3])

    def test_re_adding_same_link_is_idempotent(self):
        info = {
            'id': '123',
            'title': 'First title',
            'uploader': 'Artist',
            'webpage_url': 'https://x.com/user/status/123',
        }

        self.extractor._store_single_info(info, 'https://x.com/user/status/123', True)

        info['title'] = 'Updated title'
        success, error, _ = self.extractor._store_single_info(
            info, 'https://x.com/user/status/123', True
        )

        self.assertTrue(success, error)
        collection = self.db.get_playlist('single_twitter_123')
        self.assertEqual(collection['title'], 'Updated title')

        videos = self.db.get_playlist_videos('single_twitter_123')
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]['title'], 'Updated title')

    def test_shared_page_url_records_source_index(self):
        tweet_url = 'https://twitter.com/user/status/999'
        info = {
            '_type': 'playlist',
            'id': '999',
            'title': 'Two videos',
            'uploader': 'User',
            'entries': [
                {
                    'id': 'vid-a', 'title': 'A', 'webpage_url': tweet_url,
                    'playlist_index': 1, 'duration': 4,
                },
                {
                    'id': 'vid-b', 'title': 'B', 'webpage_url': tweet_url,
                    'playlist_index': 2, 'duration': 5,
                },
            ],
        }

        self.extractor._store_playlist_info(info, tweet_url, store_in_db=True)

        videos = self.db.get_playlist_videos('999')
        self.assertEqual([v['source_index'] for v in videos], [1, 2])
        self.assertEqual(videos[0]['url'], tweet_url)
        self.assertEqual(videos[1]['url'], tweet_url)

    def test_unique_page_url_has_no_source_index(self):
        playlist_url = 'https://www.youtube.com/playlist?list=PLx'
        info = {
            '_type': 'playlist',
            'id': 'PLx',
            'title': 'YT playlist',
            'uploader': 'Uploader',
            'entries': [
                {
                    'id': 'vid1', 'title': 'One',
                    'webpage_url': 'https://www.youtube.com/watch?v=vid1',
                    'playlist_index': 1,
                },
            ],
        }

        self.extractor._store_playlist_info(info, playlist_url, store_in_db=True)

        videos = self.db.get_playlist_videos('PLx')
        self.assertIsNone(videos[0]['source_index'])


if __name__ == '__main__':
    unittest.main()
