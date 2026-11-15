"""Regression tests for Fixes 2, 5, 6, and 8 on the :8000 Studio backend."""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from db import SpatialDatabase
from social_store import seed_everyday_social_data
import ai_services
import main as backend_main


class StudioFixesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db = backend_main.world.db
        self.orig_objects = dict(backend_main.world.objects)
        self.orig_pins = dict(backend_main.world.pins)
        self.orig_session_objs = set(backend_main.world.session_object_ids)
        self.orig_session_pins = set(backend_main.world.session_pin_ids)
        self.orig_session_events = set(backend_main.world.session_event_ids)
        self.orig_paused = backend_main.world.privacy_paused

        backend_main.world.objects.clear()
        backend_main.world.pins.clear()
        backend_main.world.session_object_ids.clear()
        backend_main.world.session_pin_ids.clear()
        backend_main.world.session_event_ids.clear()
        backend_main.world.privacy_paused = False

        self.test_db = SpatialDatabase(sqlite_path=Path(self.tmp.name) / "studio_fixes.db")
        seed_everyday_social_data(self.test_db)
        backend_main.world.db = self.test_db
        self.client = TestClient(backend_main.app)

    def tearDown(self):
        backend_main.world.db = self.orig_db
        backend_main.world.objects.clear()
        backend_main.world.objects.update(self.orig_objects)
        backend_main.world.pins.clear()
        backend_main.world.pins.update(self.orig_pins)
        backend_main.world.session_object_ids = self.orig_session_objs
        backend_main.world.session_pin_ids = self.orig_session_pins
        backend_main.world.session_event_ids = self.orig_session_events
        backend_main.world.privacy_paused = self.orig_paused
        self.test_db.close()
        self.tmp.cleanup()

    def test_fix6_token_enforcement_and_scoped_session_wipe_across_restart(self):
        # Seeded fixture objects/pins that MUST survive session wipe
        seeded_obj = backend_main.SpatialObject(
            id="obj-seeded-drill",
            label="Red DeWalt Drill",
            position=[1.0, 0.9, -1.0],
            seen_by="Alex",
            timestamp=time.time(),
            world_id="world_1",
        )
        seeded_pin = backend_main.SpatialPin(
            id="pin-seeded-work",
            author="Alex",
            text="Seeded workbench note",
            pin_type="note",
            position=[1.0, 1.0, -1.0],
            landmark="Workbench",
            created_at=time.time(),
            world_id="world_1",
        )
        backend_main.world.objects[seeded_obj.id] = seeded_obj
        backend_main.world.pins[seeded_pin.id] = seeded_pin
        self.test_db.upsert_object(seeded_obj.model_dump())
        self.test_db.upsert_pin(seeded_pin.model_dump())

        # Enable shared token via env var
        with mock.patch.dict("os.environ", {"WORLDGIT_API_TOKEN": "secret-demo-token"}):
            unauth_del = self.client.delete("/api/session")
            self.assertEqual(unauth_del.status_code, 401)
            unauth_pin = self.client.post("/api/pin-from-hud", json={"text": "x"})
            self.assertEqual(unauth_pin.status_code, 401)
            unauth_imu = self.client.post("/api/imu-sessions", json={"session_id": "s1", "samples": []})
            self.assertEqual(unauth_imu.status_code, 401)

            headers = {"X-WorldGit-Token": "secret-demo-token"}
            res_pin = self.client.post(
                "/api/pin-from-hud",
                headers=headers,
                json={"id": "hud-note-1", "label": "bottle", "text": "Session water bottle", "author": "Conor"},
            )
            self.assertEqual(res_pin.status_code, 200)
            hud_pin_id = res_pin.json()["pin"]["id"]

            res_sight = self.client.post(
                "/api/objects/sighting",
                headers=headers,
                json={"world_id": "world_1", "label": "Session Keys", "position": [0.2, 0.8, -0.5], "actor_user_id": "usr-conor"},
            )
            self.assertEqual(res_sight.status_code, 200)
            session_obj_id = res_sight.json()["object"]["id"]

            # Simulate server restart: clear in-memory session sets (DB session_records still persists!)
            backend_main.world.session_pin_ids.clear()
            backend_main.world.session_object_ids.clear()
            backend_main.world.session_event_ids.clear()

            wipe = self.client.delete("/api/session", headers=headers)
            self.assertEqual(wipe.status_code, 200)
            self.assertTrue(wipe.json()["ok"])

        # Seeded fixtures survived; session records were removed
        self.assertIn("obj-seeded-drill", backend_main.world.objects)
        self.assertIn("pin-seeded-work", backend_main.world.pins)
        self.assertNotIn(hud_pin_id, backend_main.world.pins)
        self.assertNotIn(session_obj_id, backend_main.world.objects)

    def test_fix6_privacy_pause_blocks_imu_and_sightings(self):
        pause_res = self.client.post("/api/privacy", json={"paused": True})
        self.assertEqual(pause_res.status_code, 200)
        self.assertTrue(pause_res.json()["paused"])

        imu_res = self.client.post("/api/imu-sessions", json={"session_id": "s-paused", "samples": [{"t": 1}]})
        self.assertEqual(imu_res.status_code, 409)

        sight_res = self.client.post("/api/objects/sighting", json={"label": "mug", "position": [0.0, 0.5, -1.0]})
        self.assertEqual(sight_res.status_code, 409)

        resume_res = self.client.post("/api/privacy", json={"paused": False})
        self.assertFalse(resume_res.json()["paused"])
        sight_ok = self.client.post("/api/objects/sighting", json={"label": "mug", "position": [0.0, 0.5, -1.0]})
        self.assertEqual(sight_ok.status_code, 200)

    def test_fix2_ask_localization_and_ctrlz_restore_honesty(self):
        # Stale + unlocalized sighting (position [0,0,0], old timestamp)
        stale_obj = backend_main.SpatialObject(
            id="obj-old-keys",
            label="Keys",
            position=[0.0, 0.0, 0.0],
            seen_by="Conor",
            timestamp=time.time() - 2400.0,
            world_id="world_1",
            zone_name="Desk",
        )
        backend_main.world.objects[stale_obj.id] = stale_obj
        self.test_db.upsert_object(stale_obj.model_dump())

        ask_stale = self.client.post("/api/ask", json={"query": "Where are the keys?", "wearer_id": "wearer-b"}).json()
        self.assertTrue(ask_stale["found"])
        self.assertFalse(ask_stale["localized"])
        self.assertTrue(ask_stale["stale"])
        self.assertEqual(ask_stale["arrow"], "")
        self.assertEqual(ask_stale["directions"], "")
        self.assertEqual(ask_stale["path"], [])
        self.assertNotIn("Your ", ask_stale["spoken_answer"])
        self.assertNotIn("left your", ask_stale["spoken_answer"])

        ask_missing = self.client.post("/api/ask", json={"query": "Where is my passport?", "wearer_id": "wearer-b"}).json()
        self.assertFalse(ask_missing["found"])
        self.assertIn("I don't have a recent observation", ask_missing["spoken_answer"])
        self.assertNotIn("Everything looks clear", ask_missing["spoken_answer"])

        cz = self.client.post("/api/ctrl-z").json()
        self.assertIn("undo_steps", cz)
        self.assertTrue(isinstance(cz["undo_steps"], list))

    def test_fix6_pin_from_hud_rejected_when_paused(self):
        p = self.client.post("/api/privacy", json={"paused": True})
        self.assertEqual(p.status_code, 200)
        res = self.client.post(
            "/api/pin-from-hud",
            json={"label": "Bottle", "text": "Should be discarded"},
        )
        self.assertEqual(res.status_code, 409)
        self.assertTrue(res.json()["discarded"])
        self.assertTrue(res.json()["paused"])

    def test_fix5_transcribe_honest_provider_silence_and_nonblocking_thread(self):
        pcm = b"\x00\x01" * 1600

        with mock.patch("main.transcribe_pcm_with_gemini_result", return_value={"status": "silence", "text": "", "provider": "gemini-3.8-flash", "device": "cloud", "audio_left_local": True, "disclosure": "Cloud ASR"}):
            r_sil = self.client.post("/api/transcribe", content=pcm)
            self.assertEqual(r_sil.status_code, 200)
            self.assertEqual(r_sil.json()["status"], "silence")
            self.assertEqual(r_sil.json()["text"], "")

        with mock.patch("main.transcribe_pcm_with_gemini_result", return_value={"status": "unavailable", "text": "", "provider": "gemini-3.8-flash", "device": "cloud", "audio_left_local": True, "disclosure": "Cloud ASR"}):
            r_unavail = self.client.post("/api/transcribe", content=pcm)
            self.assertEqual(r_unavail.status_code, 503)
            self.assertEqual(r_unavail.json()["status"], "unavailable")

        with mock.patch("main.transcribe_pcm_with_gemini_result", return_value={"status": "ok", "text": "find my bottle", "provider": "gemini-3.8-flash", "device": "cloud", "audio_left_local": True, "disclosure": "Cloud ASR"}):
            r_ok = self.client.post("/api/transcribe", content=pcm)
            self.assertEqual(r_ok.status_code, 200)
            body = r_ok.json()
            self.assertEqual(body["provider"], "gemini-3.8-flash")
            self.assertEqual(body["device"], "cloud")
            self.assertTrue(body["audio_left_local"])
            self.assertNotIn("parakeet", body["provider"].lower())

        # Verify async event loop isn't blocked & timeout is enforced in ai_services
        async def _verify_nonblocking_and_timeout():
            ticks = {"n": 0}

            def slow_sync(*_args, **_kwargs):
                time.sleep(0.25)
                return mock.Mock(text="delayed")

            class FakeClient:
                def __init__(self, api_key=None):
                    self.models = mock.Mock(generate_content=slow_sync)

            async def ticker():
                for _ in range(4):
                    await asyncio.sleep(0.03)
                    ticks["n"] += 1

            with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}), \
                 mock.patch.object(ai_services, "CLOUD_CALL_TIMEOUT_S", 0.08):
                with mock.patch("google.genai.Client", FakeClient):
                    res, _ = await asyncio.gather(ai_services.transcribe_pcm_with_gemini_result(pcm), ticker())
            self.assertGreaterEqual(ticks["n"], 1)
            self.assertEqual(res["status"], "unavailable")
            self.assertEqual(res["provider"], ai_services.GEMINI_MODEL)

        asyncio.run(_verify_nonblocking_and_timeout())


if __name__ == "__main__":
    unittest.main()
