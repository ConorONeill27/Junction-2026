"""Verify upload persistence and retries without a microphone or model weights."""
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request
import uuid

from detection_server import DetectionServer


class AudioUploadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = DetectionServer('127.0.0.1', 0, audio_dir=self.directory.name).start()
        self.url = f'http://127.0.0.1:{self.server.http.server_port}/api/audio-clips'
        self.body = b'\x00\x00\x00\x18ftypM4A ' + b'\x00' * 12 + b'fixture audio bytes'

    def tearDown(self):
        self.server.close()
        self.directory.cleanup()

    def post(self, body=None, clip_id=None, content_type='audio/mp4'):
        headers = {'Content-Type': content_type}
        if clip_id is not None:
            headers['X-Clip-Id'] = clip_id
        request = urllib.request.Request(self.url, self.body if body is None else body, headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_upload_during_inference_and_idempotent_retry(self):
        clip_id = str(uuid.uuid4())
        self.server.inference_lock.acquire()
        try:
            first = self.post(clip_id=clip_id)
            again = self.post(clip_id=clip_id)
        finally:
            self.server.inference_lock.release()
        self.assertEqual(first, again)
        files = list(Path(self.directory.name).iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), self.body)
        self.assertEqual(first['relative_path'], f'audioClips/{files[0].name}')

    def test_retry_cannot_overwrite_another_clip(self):
        identifier = str(uuid.uuid4())
        self.post(clip_id=identifier)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post(self.body + b'different', clip_id=identifier)
        self.assertEqual(error.exception.code, 409)

    def test_invalid_audio_and_path_traversal_rejected(self):
        for body, identifier, mime, status in [
            (b'not an audio file' * 3, None, 'audio/mp4', 400),
            (self.body, '../../outside', 'audio/mp4', 400),
            (self.body, None, 'image/jpeg', 415),
            (b'', None, 'audio/mp4', 413),
        ]:
            with self.subTest(status=status):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.post(body, identifier, mime)
                self.assertEqual(error.exception.code, status)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_duplicate_server_start_is_rejected(self):
        with self.assertRaises(OSError):
            DetectionServer('127.0.0.1', self.server.http.server_port, audio_dir=self.directory.name)


if __name__ == '__main__':
    unittest.main()
