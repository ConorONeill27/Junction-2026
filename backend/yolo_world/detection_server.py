"""LAN snapshot server for the Display Access phone app; no API keys needed."""
import argparse
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import threading
import time
from audio_clips import AudioClips, DEFAULT_AUDIO_DIR, MAX_AUDIO_BYTES


class CameraHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR permits two servers to share the same listening port.
    # Reject a duplicate start instead of routing frames to an obsolete process.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class DetectionServer:
    def __init__(self, host="0.0.0.0", port=8765, predict=None, track=None, audio_dir=DEFAULT_AUDIO_DIR):
        self.lock = threading.Lock()
        self.snapshot = {"timestamp": None, "source": "laptop_camera", "detections": []}
        owner = self
        self.inference_lock = threading.Lock()
        self.audio_clips = AudioClips(audio_dir)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(5)
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            def do_GET(self):
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

            def do_POST(self):
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
                    detections = track(frame, rotation) if track else predict(frame)
                    owner.publish(detections, [frame.shape[1], frame.shape[0]], "phone_camera")
                    with owner.lock:
                        owner.snapshot["processing_seconds"] = time.monotonic() - started
                        result = copy.deepcopy(owner.snapshot)
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
