"""Optional local Parakeet GPU worker; importing this module needs no ML packages."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading
import urllib.error
import urllib.request

MODEL_ID = 'nvidia/parakeet-unified-en-0.6b'
SAMPLE_RATE = 16000
MAX_PCM_BYTES = SAMPLE_RATE * 2 * 30


class TranscriptionUnavailable(RuntimeError):
    pass


def validate_pcm(body):
    if not 3200 <= len(body) <= MAX_PCM_BYTES or len(body) % 2:
        raise ValueError('Expected 0.1 to 30 seconds of mono 16-kHz PCM16 little-endian audio')


class ParakeetClient:
    def __init__(self, url=None):
        self.url = (url or os.getenv('PARAKEET_SERVICE_URL', 'http://127.0.0.1:8766')).rstrip('/')

    def transcribe(self, body):
        validate_pcm(body)
        request = urllib.request.Request(self.url + '/transcribe', body,
            {'Content-Type': 'application/octet-stream', 'X-Sample-Rate': str(SAMPLE_RATE)})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read(32768))
            if result.get('model') != MODEL_ID or result.get('device') != 'cuda:0':
                raise TranscriptionUnavailable('Expected Parakeet Unified 0.6B on CUDA')
            return {'text': str(result.get('text', ''))[:4000], 'model': MODEL_ID, 'device': 'cuda:0'}
        except (OSError, ValueError) as error:
            raise TranscriptionUnavailable('Parakeet GPU worker unavailable. Start start_parakeet.cmd.') from error


class ParakeetRecognizer:
    def __init__(self):
        # Heavy imports/model download happen only in the explicitly launched worker.
        print('Importing CUDA PyTorch and NeMo ASR...', flush=True)
        import torch
        import nemo.collections.asr as nemo_asr
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is required for Parakeet transcription; no CPU substitution is used')
        self.torch = torch
        print(f'Loading official weights: {MODEL_ID}', flush=True)
        self.model = nemo_asr.models.ASRModel.from_pretrained(model_name=MODEL_ID, map_location='cpu')
        self.model = self.model.to('cuda:0').eval()
        self.model.freeze()
        # CUDA inference stays enabled; skip optional graph JIT/runtime dependencies.
        from omegaconf import OmegaConf, open_dict
        decoding = OmegaConf.create(OmegaConf.to_container(self.model.cfg.decoding, resolve=True))
        with open_dict(decoding):
            decoding.strategy = 'greedy_batch'
            if 'greedy' not in decoding:
                decoding.greedy = {}
            decoding.greedy.use_cuda_graph_decoder = False
        self.model.change_decoding_strategy(decoding)
        print(f'Loaded {MODEL_ID} on {torch.cuda.get_device_name(0)}', flush=True)

    def transcribe(self, body):
        validate_pcm(body)
        import numpy as np
        samples = np.frombuffer(body, dtype='<i2').astype(np.float32) / 32768.0
        # Buffered partial transcription: latest full utterance is decoded every
        # ~500 ms. Partial text may be revised; no fabricated or OS-ASR fallback.
        with self.torch.inference_mode(), self.torch.autocast('cuda', dtype=self.torch.float16):
            result = self.model.transcribe([samples], batch_size=1, num_workers=0, verbose=False)
        if isinstance(result, tuple):
            result = result[0]
        item = result[0]
        text = item if isinstance(item, str) else item.text
        return {'text': text.strip(), 'model': MODEL_ID, 'device': 'cuda:0'}


def serve(host='127.0.0.1', port=8766):
    state = {'recognizer': None, 'error': '', 'loading': True}
    inference_lock = threading.Lock()

    def load():
        try:
            state['recognizer'] = ParakeetRecognizer()
        except Exception as error:
            import traceback
            traceback.print_exc()
            state['error'] = f'{type(error).__name__}: {error}'
            print(f'Parakeet could not load: {state["error"]}', flush=True)
        finally:
            state['loading'] = False

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def reply(self, status, result):
            data = json.dumps(result).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass  # A cancelled phone request must not interrupt the recognizer.

        def do_GET(self):
            if self.path != '/health':
                self.send_error(404)
                return
            self.reply(200, {'ready': state['recognizer'] is not None, 'loading': state['loading'],
                            'error': state['error'], 'model': MODEL_ID, 'device': 'cuda:0'})

        def do_POST(self):
            if self.path != '/transcribe':
                self.send_error(404)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 3200 <= length <= MAX_PCM_BYTES or length % 2:
                    self.send_error(413)
                    return
                body = self.rfile.read(length)
                validate_pcm(body)
                if self.headers.get('X-Sample-Rate') != str(SAMPLE_RATE):
                    raise ValueError('Expected 16-kHz audio')
                if state['recognizer'] is None:
                    self.reply(503, {'error': state['error'] or 'Parakeet is loading'})
                    return
                if not inference_lock.acquire(blocking=False):
                    self.reply(429, {'error': 'Transcription busy; send the next latest buffer'})
                    return
                try:
                    self.reply(200, state['recognizer'].transcribe(body))
                finally:
                    inference_lock.release()
            except ValueError as error:
                self.reply(400, {'error': str(error)})
            except Exception as error:
                import traceback
                traceback.print_exc()
                self.reply(503, {'error': f'Parakeet inference failed: {type(error).__name__}'})

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        allow_reuse_address = os.name != 'nt'

        def server_bind(self):
            if os.name == 'nt':
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    server = Server((host, port), Handler)
    threading.Thread(target=load, daemon=True).start()
    print(f'Parakeet worker: http://{host}:{port}/health', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    cache = Path(__file__).resolve().parent / 'settings' / 'parakeet'
    os.environ.setdefault('HF_HOME', str(cache / 'huggingface'))
    os.environ.setdefault('NEMO_CACHE_DIR', str(cache / 'nemo'))
    serve(args.host, args.port)
