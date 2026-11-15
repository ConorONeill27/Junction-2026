"""Regression tests for Fixes 1-6 on the :8765 Vision server."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from detection_server import DetectionServer  # noqa: E402
from object_memory import ObjectMemoryStore  # noqa: E402
from spatial_notes import SpatialNoteStore  # noqa: E402


def _http_json(url: str, method: str = "GET", payload: dict | None = None, headers: dict | None = None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req_headers = dict(headers or {})
    if payload is not None:
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


class VisionFixesTests(unittest.TestCase):
    def test_fix1_and_fix2_unfamiliar_memory_ask_and_honest_missing_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = ObjectMemoryStore(Path(tmp) / "memory", studio_url="")
            self.assertEqual(mem.items, {})

            frame1 = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.rectangle(frame1, (20, 40), (120, 180), (200, 120, 40), -1)
            cv2.rectangle(frame1, (150, 90), (280, 220), (60, 140, 60), -1)
            mem.observe(
                [
                    {"object_id": "umb-1", "label": "umbrella", "confidence": 0.91, "bbox_xyxy": [20, 40, 120, 180]},
                    {"object_id": "desk-1", "label": "desk", "confidence": 0.89, "bbox_xyxy": [150, 90, 280, 220]},
                ],
                frame1,
            )
            for item in mem.items.values():
                item["last_seen"] = time.time() - 125.0

            # Empty view later
            mem.observe([], np.zeros((240, 320, 3), dtype=np.uint8))

            found = mem.ask("Where is the umbrella?")
            self.assertTrue(found["found"])
            self.assertIn("Last seen", found["spoken_response"])
            self.assertNotIn("Your umbrella is", found["spoken_response"])
            self.assertIn("minutes ago", found["age_label"])
            self.assertIn("desk", found["context"])

            missing = mem.ask("Where are my keys?")
            self.assertFalse(missing["found"])
            self.assertIn("I don't have a recent observation", missing["spoken_response"])
            self.assertNotIn("Everything looks clear", missing["spoken_response"])

    def test_fix4_two_bottles_no_label_leak_and_3d_pose_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes_dir = Path(tmp) / "notes"
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.rectangle(frame, (30, 50), (100, 190), (240, 40, 20), -1)   # Bottle 1: blue in BGR
            cv2.rectangle(frame, (200, 50), (270, 190), (20, 30, 240), -1) # Bottle 2: red in BGR

            phase = {"step": 1}

            def fake_predict(_img):
                if phase["step"] == 1:
                    return [
                        {"object_id": "b1", "label": "bottle", "confidence": 0.94, "bbox_xyxy": [30.0, 50.0, 100.0, 190.0]},
                        {"object_id": "b2", "label": "bottle", "confidence": 0.92, "bbox_xyxy": [200.0, 50.0, 270.0, 190.0]},
                    ]
                # Shifted viewpoint with brand-new IDs
                return [
                    {"object_id": "b1-new", "label": "bottle", "confidence": 0.93, "bbox_xyxy": [45.0, 55.0, 115.0, 195.0]},
                    {"object_id": "b2-new", "label": "bottle", "confidence": 0.91, "bbox_xyxy": [215.0, 55.0, 285.0, 195.0]},
                ]

            server = DetectionServer(
                "127.0.0.1",
                0,
                predict=fake_predict,
                notes_dir=notes_dir,
                memory_dir=Path(tmp) / "memory",
                places_dir=Path(tmp) / "places",
                audio_dir=Path(tmp) / "audio",
                studio_url="",
            )
            server.notes = SpatialNoteStore(notes_dir, studio_url="")
            server.start()
            port = server.http.server_address[1]
            try:
                ok, jpg = cv2.imencode(".jpg", frame)
                self.assertTrue(ok)
                req1 = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/frame",
                    data=jpg.tobytes(),
                    headers={"Content-Type": "image/jpeg", "X-Tracking-Mode": "phone"},
                    method="POST",
                )
                with urllib.request.urlopen(req1, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)

                # Pin note ONLY onto Bottle 1 ("b1")
                status, saved = _http_json(
                    f"http://127.0.0.1:{port}/api/notes",
                    method="POST",
                    payload={"object_id": "b1", "text": "Electrolytes", "author": "Conor"},
                )
                self.assertEqual(status, 200)
                self.assertTrue(saved["ok"])
                self.assertTrue(saved["note_id"])

                # Shifted viewpoint: Bottle 1 keeps note, Bottle 2 must NOT inherit it
                phase["step"] = 2
                frame2 = np.zeros((240, 320, 3), dtype=np.uint8)
                cv2.rectangle(frame2, (45, 55), (115, 195), (240, 40, 20), -1)
                cv2.rectangle(frame2, (215, 55), (285, 195), (20, 30, 240), -1)
                ok2, jpg2 = cv2.imencode(".jpg", frame2)
                self.assertTrue(ok2)
                req2 = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/frame",
                    data=jpg2.tobytes(),
                    headers={"Content-Type": "image/jpeg", "X-Tracking-Mode": "phone"},
                    method="POST",
                )
                with urllib.request.urlopen(req2, timeout=5) as resp:
                    d2 = json.loads(resp.read().decode("utf-8"))
                by_id = {d["object_id"]: d for d in d2["detections"]}
                self.assertEqual(by_id["b1-new"].get("note"), "Electrolytes")
                self.assertFalse(by_id["b1-new"].get("note_uncertain", False))
                self.assertIsNone(by_id["b2-new"].get("note"))

                # Missing-crop weak fallback should not propagate to two ambiguous bottles
                store = SpatialNoteStore(Path(tmp) / "weak_notes", studio_url="")
                store.add_note(label="bottle", text="Check cap", sync_studio=False)
                out_two = store.annotate_detections(
                    [
                        {"object_id": "x1", "label": "bottle", "bbox_xyxy": [30, 50, 100, 190]},
                        {"object_id": "x2", "label": "bottle", "bbox_xyxy": [200, 50, 270, 190]},
                    ],
                    frame2,
                )
                self.assertFalse(any(d.get("note") for d in out_two))
                # Single bottle with missing crop gets weak uncertainty prefix
                out_one = store.annotate_detections(
                    [{"object_id": "x1", "label": "bottle", "bbox_xyxy": [30, 50, 100, 190]}],
                    frame2,
                )
                self.assertTrue(out_one[0]["note_uncertain"])
                self.assertIn("Possible note for this bottle", out_one[0]["note"])

                # Camera pose headers on /api/frame exercise 3D projection branch
                server.notes.add_note(
                    label="whiteboard",
                    text="Standup 10am",
                    position_3d=[0.0, 0.0, 2.0],
                    sync_studio=False,
                )
                phase_empty_img = np.zeros((480, 640, 3), dtype=np.uint8)
                ok3, jpg3 = cv2.imencode(".jpg", phase_empty_img)
                self.assertTrue(ok3)

                def no_dets(_img):
                    return []

                server.predict = no_dets
                req3 = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/frame",
                    data=jpg3.tobytes(),
                    headers={
                        "Content-Type": "image/jpeg",
                        "X-Camera-Position": "0.0,0.0,0.0",
                        "X-Camera-Rotation": "1,0,0,0,1,0,0,0,1",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req3, timeout=5) as resp:
                    d3 = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(any(d.get("anchor_source") == "3d_projection" and d.get("note") == "Standup 10am" for d in d3["detections"]))
            finally:
                server.close()

    def test_fix3_fix5_fix6_status_and_privacy_guards(self):
        class MockStudioHandler(BaseHTTPRequestHandler):
            mode = {"transcribe": "cloud"}

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                _ = self.rfile.read(length)
                if self.path == "/api/pin-from-hud":
                    body = json.dumps({"ok": True, "pin_id": "pin-1", "position": [0.1, 0.9, -1.2]}).encode("utf-8")
                    self.send_response(200)
                elif self.path == "/api/transcribe":
                    body = json.dumps({
                        "ok": True,
                        "status": "ok",
                        "text": "where is my bottle",
                        "provider": "gemini-3.8-flash",
                        "device": "cloud",
                        "audio_left_local": True,
                        "disclosure": "Audio sent to Google Gemini cloud for transcription.",
                    }).encode("utf-8")
                    self.send_response(200)
                else:
                    body = json.dumps({"ok": True}).encode("utf-8")
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_DELETE(self):
                body = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        studio = HTTPServer(("127.0.0.1", 0), MockStudioHandler)
        threading.Thread(target=studio.serve_forever, daemon=True).start()
        studio_url = f"http://127.0.0.1:{studio.server_address[1]}"

        try:
            with tempfile.TemporaryDirectory() as tmp:
                notes_dir = Path(tmp) / "notes"
                server = DetectionServer(
                    "127.0.0.1",
                    0,
                    predict=lambda _img: [],
                    notes_dir=notes_dir,
                    memory_dir=Path(tmp) / "memory",
                    places_dir=Path(tmp) / "places",
                    audio_dir=Path(tmp) / "audio",
                    studio_url=studio_url,
                )
                server.start()
                port = server.http.server_address[1]
                try:
                    # Cloud transcription attribution passes through without fake Parakeet label
                    pcm = b"\x01\x02" * 1600
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/transcribe",
                        data=pcm,
                        headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": "16000"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        tr = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(tr["provider"], "gemini-3.8-flash")
                    self.assertEqual(tr["device"], "cloud")
                    self.assertTrue(tr["audio_left_local"])

                    # With studio_url cleared & no local ASR, /api/transcribe returns 503 (never fake empty 200)
                    server.studio_url = ""
                    req_fail = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/transcribe",
                        data=pcm,
                        headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": "16000"},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(req_fail, timeout=5)
                    self.assertEqual(ctx.exception.code, 503)

                    # Restore studio_url, pause privacy -> in-flight/stale note & audio saves rejected (409)
                    server.studio_url = studio_url
                    _http_json(f"http://127.0.0.1:{port}/api/privacy", method="POST", payload={"paused": True})
                    with self.assertRaises(urllib.error.HTTPError) as note_ctx:
                        _http_json(
                            f"http://127.0.0.1:{port}/api/notes",
                            method="POST",
                            payload={"label": "cup", "text": "Should fail while paused"},
                        )
                    self.assertEqual(note_ctx.exception.code, 409)

                    aud_req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/audio-clips",
                        data=b"ftypmp42" + b"\x00" * 32,
                        headers={
                            "Content-Type": "audio/mp4",
                            "X-Session-Epoch": str(server.privacy_epoch),
                        },
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as aud_ctx:
                        urllib.request.urlopen(aud_req, timeout=5)
                    self.assertEqual(aud_ctx.exception.code, 409)

                    # Resume privacy, save note with old epoch -> 409 discarded
                    _http_json(f"http://127.0.0.1:{port}/api/privacy", method="POST", payload={"paused": False})
                    with self.assertRaises(urllib.error.HTTPError) as stale_ctx:
                        _http_json(
                            f"http://127.0.0.1:{port}/api/notes",
                            method="POST",
                            payload={"label": "cup", "text": "Stale epoch", "session_epoch": 0},
                        )
                    self.assertEqual(stale_ctx.exception.code, 409)

                    # Save with current epoch -> succeeds with studio_synced=True
                    _, saved = _http_json(
                        f"http://127.0.0.1:{port}/api/notes",
                        method="POST",
                        payload={"label": "cup", "text": "Fresh epoch"},
                    )
                    self.assertTrue(saved["ok"])
                    self.assertTrue(saved["studio_synced"])

                    # Session wipe reports studio_cleared=True when Studio confirms, False when unreachable
                    _, wiped = _http_json(f"http://127.0.0.1:{port}/api/session", method="DELETE")
                    self.assertTrue(wiped["ok"])
                    self.assertTrue(wiped["studio_cleared"])

                    server.studio_url = "http://127.0.0.1:1"
                    _, wiped_partial = _http_json(f"http://127.0.0.1:{port}/api/session", method="DELETE")
                    self.assertTrue(wiped_partial["ok"])
                    self.assertFalse(wiped_partial["studio_cleared"])

                    # Unpaused audio upload with stale X-Session-Epoch must return 409 and write nothing
                    stale_aud_req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/audio-clips",
                        data=b"ftypmp42" + b"\x00" * 32,
                        headers={
                            "Content-Type": "audio/mp4",
                            "X-Session-Epoch": "0",
                        },
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as stale_aud_ctx:
                        urllib.request.urlopen(stale_aud_req, timeout=5)
                    self.assertEqual(stale_aud_ctx.exception.code, 409)
                    self.assertEqual(list((Path(tmp) / "audio").glob("*")), [])
                finally:
                    server.close()
        finally:
            studio.shutdown()
            studio.server_close()

    def test_concurrent_note_save_aborted_by_pause_or_wipe_rolls_back_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            server_ref = {}

            class RaceStudioHandler(BaseHTTPRequestHandler):
                def do_POST(self):
                    length = int(self.headers.get("Content-Length", "0"))
                    _ = self.rfile.read(length)
                    # Simulate user hitting Pause/Wipe while Studio forwarding is in-flight!
                    srv = server_ref["server"]
                    with srv.lock:
                        srv.privacy_epoch += 1
                        srv.memory.set_paused(True)
                    body = json.dumps({"ok": True, "pin_id": "pin-race"}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass

            studio = HTTPServer(("127.0.0.1", 0), RaceStudioHandler)
            threading.Thread(target=studio.serve_forever, daemon=True).start()
            studio_url = f"http://127.0.0.1:{studio.server_address[1]}"
            try:
                notes_dir = Path(tmp) / "notes"
                server = DetectionServer(
                    "127.0.0.1",
                    0,
                    predict=lambda _img: [],
                    notes_dir=notes_dir,
                    memory_dir=Path(tmp) / "memory",
                    places_dir=Path(tmp) / "places",
                    audio_dir=Path(tmp) / "audio",
                    studio_url=studio_url,
                )
                server_ref["server"] = server
                server.start()
                port = server.http.server_address[1]
                try:
                    with self.assertRaises(urllib.error.HTTPError) as race_ctx:
                        _http_json(
                            f"http://127.0.0.1:{port}/api/notes",
                            method="POST",
                            payload={"label": "bottle", "text": "In-flight race note"},
                        )
                    self.assertEqual(race_ctx.exception.code, 409)
                    # Note must be rolled back from memory AND persisted notes.json!
                    self.assertEqual(server.notes.list_notes(), [])
                    persisted = json.loads((notes_dir / "notes.json").read_text(encoding="utf-8"))
                    self.assertEqual(persisted.get("notes"), [])
                finally:
                    server.close()
            finally:
                studio.shutdown()
                studio.server_close()


if __name__ == "__main__":
    unittest.main()
