"""Non-blocking background bridge from yolo_world (:8765) to WorldGit Studio (:8000)."""

import json
import os
import threading
import urllib.error
import urllib.request


DEFAULT_STUDIO_URL = os.getenv("WORLDGIT_STUDIO_URL", "http://127.0.0.1:8000").rstrip("/")


def post_json_async(base_url, path, payload, timeout=2.5, headers=None, cancel_check=None):
    """Fire-and-forget JSON POST to the WorldGit Studio backend."""
    if not base_url:
        return None
    if cancel_check and cancel_check():
        return None
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    token = os.getenv("WORLDGIT_API_TOKEN", "").strip()
    if token:
        req_headers["X-WorldGit-Token"] = token
    if headers:
        req_headers.update(headers)

    def _send():
        if cancel_check and cancel_check():
            return
        req = urllib.request.Request(
            url,
            data=data,
            headers=req_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout):
                pass
        except (urllib.error.URLError, OSError, TimeoutError):
            pass

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    return thread
