"""LAN snapshot server for the Display Access phone app; no API keys needed."""
import argparse
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse
from audio_clips import AudioClips, DEFAULT_AUDIO_DIR, MAX_AUDIO_BYTES
from object_memory import DEFAULT_MEMORY_DIR, ObjectMemoryStore
from parakeet_asr import ParakeetClient, TranscriptionUnavailable, MAX_PCM_BYTES, validate_pcm
from place_anchors import DEFAULT_PLACES_DIR, PlaceAnchorStore
from spatial_notes import DEFAULT_NOTES_DIR, SpatialNoteStore


class CameraHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR permits two servers to share the same listening port.
    # Reject a duplicate start instead of routing frames to an obsolete process.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class DetectionServer:
    def __init__(self, host="0.0.0.0", port=8765, predict=None, track=None, audio_dir=DEFAULT_AUDIO_DIR, transcribe=None, notes_dir=DEFAULT_NOTES_DIR, places_dir=DEFAULT_PLACES_DIR, memory_dir=DEFAULT_MEMORY_DIR):
        self.lock = threading.Lock()
        self.snapshot = {"timestamp": None, "source": "laptop_camera", "detections": []}
        owner = self
        self.inference_lock = threading.Lock()
        self.audio_clips = AudioClips(audio_dir)
        self.transcribe = transcribe or ParakeetClient().transcribe
        self.notes = SpatialNoteStore(notes_dir)
        self.places = PlaceAnchorStore(places_dir)
        self.memory = ObjectMemoryStore(memory_dir)
        self.latest_frame = None
        self.latest_detections = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _send_json(self, obj, status=200):
                data = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def setup(self):
                super().setup()
                self.connection.settimeout(5)
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            def do_GET(self):
                if self.path == "/api/calibration":
                    cal_path = Path(__file__).resolve().parent / "hud_calibration.json"
                    data = cal_path.read_bytes() if cal_path.exists() else b"{}"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if self.path == "/api/notes":
                    self._send_json({"notes": owner.notes.list_notes()})
                    return
                if self.path == "/api/places":
                    self._send_json({"places": owner.places.list_places()})
                    return
                if self.path == "/api/memory":
                    self._send_json({"memories": owner.memory.list_memories(), "paused": owner.memory.paused})
                    return
                if self.path.startswith("/api/memory/find"):
                    qs = parse_qs(urlparse(self.path).query)
                    q = (qs.get("q") or [""])[0]
                    hit = owner.memory.find(q)
                    if hit is None:
                        self._send_json({"found": False, "query": q, "summary": f"No memory found for '{q}'"}, status=404)
                    else:
                        self._send_json({"found": True, **hit})
                    return
                if self.path.startswith("/api/memory/ask"):
                    qs = parse_qs(urlparse(self.path).query)
                    q = (qs.get("q") or [""])[0]
                    self._send_json({"ok": True, **owner.memory.ask(q)})
                    return
                if self.path == "/api/privacy":
                    self._send_json({"paused": owner.memory.paused})
                    return
                if self.path != "/api/detections":
                    self.send_error(404)
                    return
                with owner.lock:
                    data = json.dumps(owner.snapshot).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_DELETE(self):
                if self.path == "/api/notes":
                    owner.notes.clear()
                    self._send_json({"ok": True, "notes": []})
                    return
                if self.path == "/api/places":
                    owner.places.clear()
                    self._send_json({"ok": True, "places": []})
                    return
                if self.path == "/api/memory":
                    owner.memory.clear()
                    self._send_json({"ok": True, "memories": []})
                    return
                self.send_error(404)

            def do_POST(self):
                if self.path == "/api/calibration":
                    self.save_calibration()
                    return
                if self.path == "/api/notes":
                    self.save_note()
                    return
                if self.path == "/api/places":
                    self.save_place()
                    return
                if self.path == "/api/privacy":
                    self.toggle_privacy()
                    return
                if self.path == "/api/memory/ask":
                    self.ask_memory()
                    return
                if self.path == '/api/transcribe':
                    self.transcribe_audio()
                    return
                if self.path == '/api/audio-clips':
                    self.save_audio_clip()
                    return
                if self.path != "/api/frame" or (predict is None and track is None):
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2_000_000:
                    self.send_error(413)
                    return
                if not owner.inference_lock.acquire(blocking=False):
                    self.send_error(429, "Inference busy; drop this frame")
                    return
                try:
                    import cv2
                    import numpy as np
                    frame = cv2.imdecode(np.frombuffer(self.rfile.read(length), dtype=np.uint8), cv2.IMREAD_COLOR)
                    if frame is None or max(frame.shape[:2]) > 4096:
                        self.send_error(400, "Invalid camera JPEG")
                        return
                    image_rotation = int(self.headers.get("X-Image-Rotation", "0"))
                    if image_rotation not in (0, 90, 180, 270):
                        raise ValueError("Invalid image rotation")
                    if image_rotation:
                        frame = cv2.rotate(frame, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                                                   270: cv2.ROTATE_90_COUNTERCLOCKWISE}[image_rotation])
                    started = time.monotonic()
                    rotation = None
                    raw_rotation = self.headers.get("X-Camera-Rotation")
                    if raw_rotation:
                        rotation = np.array([float(v) for v in raw_rotation.split(",")]).reshape(3, 3)
                        if not np.isfinite(rotation).all() or not np.allclose(rotation.T @ rotation, np.eye(3), atol=0.02) or np.linalg.det(rotation) < 0.98:
                            raise ValueError("Invalid rotation")
                    phone_tracking = self.headers.get("X-Tracking-Mode") == "phone" and predict is not None
                    detections = predict(frame) if phone_tracking else track(frame, rotation) if track else predict(frame)
                    owner.memory.observe(detections, frame)
                    detections = owner.notes.annotate_detections(detections, frame)
                    detections.extend(owner.places.match_frame(frame))
                    with owner.lock:
                        owner.latest_frame = frame.copy()
                        owner.latest_detections = list(detections)
                    owner.publish(detections, [frame.shape[1], frame.shape[0]], "phone_camera")
                    with owner.lock:
                        owner.snapshot["processing_seconds"] = time.monotonic() - started
                        result = copy.deepcopy(owner.snapshot)
                    result["tracking_mode"] = "phone" if phone_tracking else "server"
                    result["inference_seconds"] = time.monotonic() - started
                    data = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (ValueError, OSError):
                    self.send_error(400, "Invalid frame request")
                except Exception:
                    self.send_error(503, "Inference unavailable")
                finally:
                    owner.inference_lock.release()

            def save_audio_clip(self):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 24 <= length <= MAX_AUDIO_BYTES:
                        self.send_error(413, 'Audio clip must be between 24 bytes and 15 MB')
                        return
                    if self.headers.get('Content-Type', '').split(';')[0] != 'audio/mp4':
                        self.send_error(415, 'Expected audio/mp4')
                        return
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise ValueError('Incomplete audio clip')
                    result = owner.audio_clips.save(body, self.headers.get('X-Clip-Id'))
                    data = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except FileExistsError:
                    self.send_error(409, 'Clip ID already used')
                except (ValueError, OSError):
                    self.send_error(400, 'Unable to save audio clip')

            def transcribe_audio(self):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 3200 <= length <= MAX_PCM_BYTES or length % 2:
                        self.send_error(413, 'Expected 0.1 to 30 seconds of PCM16 audio')
                        return
                    if self.headers.get('Content-Type') != 'application/octet-stream' or self.headers.get('X-Sample-Rate') != '16000':
                        self.send_error(415, 'Expected mono 16-kHz PCM16 little-endian')
                        return
                    body = self.rfile.read(length)
                    validate_pcm(body)
                    data = json.dumps(owner.transcribe(body)).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(data)
                except TranscriptionUnavailable as error:
                    data = json.dumps({'error': str(error)}).encode()
                    self.send_response(503)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (OSError, ValueError):
                    self.send_error(400, 'Invalid transcription audio')

            def toggle_privacy(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
                    paused = owner.memory.set_paused(bool(payload.get("paused", not owner.memory.paused)))
                    self._send_json({"ok": True, "paused": paused})
                except (OSError, ValueError, TypeError):
                    self.send_error(400, "Invalid privacy payload")

            def ask_memory(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if 0 < length <= 65536 else {}
                    question = str(payload.get("question") or payload.get("q") or "")
                    self._send_json({"ok": True, **owner.memory.ask(question)})
                except (OSError, ValueError, TypeError):
                    self.send_error(400, "Invalid memory query payload")

            def save_place(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 2 <= length <= 2_500_000:
                        self.send_error(400, "Invalid place payload")
                        return
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    with owner.lock:
                        latest_frame = owner.latest_frame.copy() if owner.latest_frame is not None else None
                    record = owner.places.add_place(
                        name=payload.get("name", ""),
                        rating=payload.get("rating", 4),
                        review=payload.get("review", ""),
                        author=payload.get("author", "Friend"),
                        image_bgr=None if payload.get("image_b64") else latest_frame,
                        image_b64=payload.get("image_b64"),
                    )
                    self._send_json({"ok": True, "place": record})
                except (OSError, ValueError, TypeError):
                    self.send_error(400, "Invalid place request")

            def save_note(self):
                try:
                    import cv2
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 2 <= length <= 500_000:
                        self.send_error(400, "Invalid note payload")
                        return
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    object_id = payload.get("object_id")
                    label = payload.get("label") or "object"
                    bbox = payload.get("bbox_xyxy")
                    frame_size = payload.get("frame_size_px")
                    crop_bgr = None
                    with owner.lock:
                        latest_frame = owner.latest_frame
                        latest_dets = list(owner.latest_detections)
                    if object_id:
                        for d in latest_dets:
                            if d.get("object_id") == object_id:
                                label = payload.get("label") or d.get("label", label)
                                bbox = bbox or d.get("bbox_xyxy")
                                break
                    if latest_frame is not None and bbox and len(bbox) == 4:
                        fh, fw = latest_frame.shape[:2]
                        frame_size = frame_size or [fw, fh]
                        x1, y1, x2, y2 = [int(v) for v in bbox]
                        l, t = max(0, x1), max(0, y1)
                        r, b = min(fw, x2), min(fh, y2)
                        if r > l and b > t:
                            crop_bgr = latest_frame[t:b, l:r].copy()
                    if crop_bgr is None and object_id:
                        obj_img = Path(__file__).resolve().parent / "objects" / str(object_id) / "image.jpg"
                        if obj_img.exists():
                            crop_bgr = cv2.imread(str(obj_img))
                    record = owner.notes.add_note(
                        label=label,
                        text=payload.get("text", ""),
                        author=payload.get("author", "Person A"),
                        object_id=object_id,
                        bbox_xyxy=bbox,
                        frame_size_px=frame_size,
                        crop_bgr=crop_bgr,
                        position_3d=payload.get("position_3d"),
                    )
                    self._send_json({"ok": True, "note": record})
                except (OSError, ValueError, TypeError):
                    self.send_error(400, "Invalid note request")

            def save_calibration(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 2 <= length <= 8192:
                        self.send_error(400, "Invalid calibration payload")
                        return
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    cal = {
                        "size": float(payload.get("size", 1.0)),
                        "horizontal": float(payload.get("horizontal", 0.0)),
                        "vertical": float(payload.get("vertical", 0.0)),
                        "roll": float(payload.get("roll", 0.0)),
                        "saved_at": time.time(),
                    }
                    cal_path = Path(__file__).resolve().parent / "hud_calibration.json"
                    cal_path.write_text(json.dumps(cal, indent=2) + "\n", encoding="utf-8")
                    print(f"[HUD Calibration Saved] size={cal['size']:.3f} x={cal['horizontal']:.1f} y={cal['vertical']:.1f} roll={cal['roll']:.2f}", flush=True)
                    data = json.dumps({"ok": True, "calibration": cal}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (OSError, ValueError, TypeError):
                    self.send_error(400, "Invalid calibration JSON")

            def log_message(self, *args):
                pass

        self.http = CameraHTTPServer((host, port), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def publish(self, detections, frame_size=None, source="laptop_camera"):
        with self.lock:
            self.snapshot = {"timestamp": time.time(), "source": source,
                             "frame_size_px": frame_size, "detections": copy.deepcopy(detections),
                             "supports_image_rotation": True}

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve clearly marked sample detections without a camera")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = DetectionServer(port=args.port).start()
    print(f"Sample detections at http://<laptop-ip>:{args.port}/api/detections", flush=True)
    try:
        while True:
            server.publish([{"label": label, "object_id": f"sample-{i}", "confidence": confidence,
                             "bbox_xyxy": box} for i, (label, confidence, box) in enumerate([
                                 ("person", 0.94, [100, 50, 300, 450]),
                                 ("bottle", 0.88, [350, 180, 420, 360]),
                                 ("laptop", 0.91, [430, 200, 630, 400])])], [640, 480], "sample")
            time.sleep(1.5)
    except KeyboardInterrupt:
        server.close()
