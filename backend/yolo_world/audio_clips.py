"""Persist phone microphone recordings separately from the frame-processing lock."""
from pathlib import Path
import tempfile
import threading
import uuid
import os

DEFAULT_AUDIO_DIR = Path(__file__).resolve().parent / 'audioClips'
MAX_AUDIO_BYTES = 15_000_000


class AudioClips:
    def __init__(self, directory=DEFAULT_AUDIO_DIR):
        self.directory = Path(directory)
        self.lock = threading.Lock()

    def save(self, body, clip_id=None):
        if len(body) < 24 or body[4:8] != b'ftyp':
            raise ValueError('Expected an M4A/MP4 recording')
        if len(body) > MAX_AUDIO_BYTES:
            raise ValueError('Recording exceeds upload limit')
        identifier = uuid.UUID(clip_id).hex if clip_id else uuid.uuid4().hex
        filename = f'clip_{identifier}.m4a'
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.directory / filename
            if target.exists():
                if target.read_bytes() != body:
                    raise FileExistsError('Clip ID already contains a different recording')
            else:
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.directory, suffix='.tmp', delete=False) as output:
                        temporary = Path(output.name)
                        output.write(body)
                    os.replace(temporary, target)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        return {'status': 'saved', 'filename': filename,
                'relative_path': f'audioClips/{filename}', 'bytes': len(body)}
cah