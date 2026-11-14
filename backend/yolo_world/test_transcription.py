import io
import json
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from detection_server import DetectionServer
from parakeet_asr import MODEL_ID, MAX_PCM_BYTES, ParakeetClient, TranscriptionUnavailable, validate_pcm


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        def recognize(body):
            self.received.append(body)
            return {'text': 'test partial', 'model': MODEL_ID, 'device': 'cuda:0'}
        self.server = DetectionServer('127.0.0.1', 0, transcribe=recognize).start()
        self.url = f'http://127.0.0.1:{self.server.http.server_port}/api/transcribe'

    def tearDown(self):
        self.server.close()

    def post(self, body, rate='16000'):
        request = urllib.request.Request(self.url, body,
            {'Content-Type': 'application/octet-stream', 'X-Sample-Rate': rate})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_audio_is_forwarded_without_blocking_yolo_inference_lock(self):
        body = b'\x01\x00' * 16000
        self.server.inference_lock.acquire()
        try:
            result = self.post(body)
        finally:
            self.server.inference_lock.release()
        self.assertEqual(self.received, [body])
        self.assertEqual(result['text'], 'test partial')
        self.assertEqual(result['model'], MODEL_ID)

    def test_invalid_audio_and_sample_rate_rejected(self):
        for body, rate, status in [(b'', '16000', 413), (b'\0' * 3201, '16000', 413),
                                   (b'\0' * 3200, '48000', 415)]:
            with self.subTest(rate=rate, size=len(body)):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.post(body, rate)
                self.assertEqual(error.exception.code, status)
        self.assertEqual(self.received, [])

    def test_missing_gpu_worker_returns_explicit_error_without_fake_text(self):
        def unavailable(body):
            raise TranscriptionUnavailable('GPU worker unavailable')
        self.server.transcribe = unavailable
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post(b'\0' * 3200)
        self.assertEqual(error.exception.code, 503)
        result = json.load(error.exception)
        self.assertIn('error', result)
        self.assertNotIn('text', result)

    def test_client_rejects_another_model_or_cpu_substitution(self):
        for model, device in [('other-model', 'cuda:0'), (MODEL_ID, 'cpu')]:
            response = io.BytesIO(json.dumps({'text': 'untrusted', 'model': model, 'device': device}).encode())
            with patch('parakeet_asr.urllib.request.urlopen', return_value=response):
                with self.assertRaises(TranscriptionUnavailable):
                    ParakeetClient().transcribe(b'\0' * 3200)

    def test_bounded_audio_duration(self):
        validate_pcm(b'\0' * 3200)
        with self.assertRaises(ValueError):
            validate_pcm(b'\0' * (MAX_PCM_BYTES + 2))


if __name__ == '__main__':
    unittest.main()
