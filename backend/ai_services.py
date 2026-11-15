"""
AI services for WorldGit:
- Google Gemini API: semantic frame object detection & Ctrl+Z undo synthesis
- OpenAI API: reasoning fallback
- ElevenLabs API: whispered spatial TTS with browser SpeechSynthesis fallback
"""
import base64
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import httpx

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH, override=True)
GEMINI_MODEL = "gemini-3.8-flash"


def _get_gemini_key() -> str:
    load_dotenv(_ENV_PATH, override=True)
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def _extract_json_array(raw_text: str) -> List[Dict[str, Any]]:
    match = re.search(r"\[[\s\S]*\]", raw_text)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


async def analyze_frame_with_gemini(
    image_b64: str,
    wearer_id: str,
    pose: List[float],
) -> Dict[str, Any]:
    """
    Detect objects and hazards in an incoming first-person frame using Gemini 2.5 Flash,
    falling back to OpenAI GPT-4o-mini or deterministic detection when keys are absent.
    """
    gemini_key = _get_gemini_key()
    openai_key = os.getenv("OPENAI_API_KEY")
    clean_b64 = image_b64.split(",", 1)[1] if "," in image_b64 else image_b64

    prompt = (
        "Identify up to 3 notable physical objects or workspace items in this first-person frame. "
        "Return ONLY a JSON array of objects with keys: "
        'label (string), rel_x (float meters, -1.5 to 1.5), rel_y (float meters, -0.5 to 0.5), '
        "rel_z (float meters forward, -2.5 to -0.5), confidence (float 0..1)."
    )

    if gemini_key and clean_b64:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=gemini_key)
            img_bytes = base64.b64decode(clean_b64)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                    prompt,
                ],
            )
            detections = _extract_json_array(response.text or "")
            if detections:
                return {"provider": "gemini-2.5-flash", "detections": detections}
        except Exception as exc:
            gemini_err = str(exc)
        else:
            gemini_err = None
    else:
        gemini_err = None

    if openai_key and clean_b64:
        try:
            from openai import OpenAI

            oai = OpenAI(api_key=openai_key)
            resp = oai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{clean_b64}"},
                            },
                        ],
                    }
                ],
                max_tokens=300,
            )
            text = resp.choices[0].message.content or ""
            detections = _extract_json_array(text)
            if detections:
                return {"provider": "openai-gpt-4o-mini-fallback", "detections": detections}
        except Exception:
            pass

    # Deterministic fallback for live simulator/offline demos
    return {
        "provider": "deterministic-sim" if not gemini_err else f"sim-fallback ({gemini_err[:40]})",
        "detections": [
            {
                "label": "Soldering Iron",
                "rel_x": 0.4,
                "rel_y": -0.3,
                "rel_z": -1.4,
                "confidence": 0.91,
            },
            {
                "label": "Safety Goggles",
                "rel_x": -0.5,
                "rel_y": -0.2,
                "rel_z": -1.1,
                "confidence": 0.88,
            },
        ],
    }


async def generate_ctrl_z_instructions(diff: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a raw 3D Reality git diff into concise spoken Ctrl+Z undo steps."""
    deterministic: List[str] = []
    for m in diff.get("moved", []):
        deterministic.append(
            f"Move {m['label']} back {m['distance_m']}m to ({m['from'][0]:.1f}, {m['from'][2]:.1f})."
        )
    for r in diff.get("removed", []):
        deterministic.append(
            f"Return missing {r['label']} to ({r['position'][0]:.1f}, {r['position'][2]:.1f})."
        )
    for a in diff.get("added", []):
        deterministic.append(f"Remove newly placed {a['label']}.")

    if not deterministic:
        return {
            "provider": "deterministic",
            "undo_steps": ["Scene matches previous snapshot. No undo needed."],
        }

    gemini_key = _get_gemini_key()
    openai_key = os.getenv("OPENAI_API_KEY")
    instruction_prompt = (
        "Turn this physical room diff into concise, numbered spoken step-by-step instructions "
        "for a smart glasses wearer to restore the room (Ctrl+Z). Return ONLY a JSON array of strings.\n"
        f"Diff: {json.dumps(diff)}"
    )

    if gemini_key:
        try:
            from google import genai

            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=instruction_prompt,
            )
            match = re.search(r"\[[\s\S]*\]", resp.text or "")
            if match:
                steps = json.loads(match.group(0))
                if isinstance(steps, list) and steps:
                    return {"provider": "gemini-2.5-flash", "undo_steps": [str(s) for s in steps]}
        except Exception:
            pass

    if openai_key:
        try:
            from openai import OpenAI

            oai = OpenAI(api_key=openai_key)
            resp = oai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": instruction_prompt}],
                max_tokens=220,
            )
            match = re.search(r"\[[\s\S]*\]", resp.choices[0].message.content or "")
            if match:
                steps = json.loads(match.group(0))
                if isinstance(steps, list) and steps:
                    return {"provider": "openai-gpt-4o-mini", "undo_steps": [str(s) for s in steps]}
        except Exception:
            pass

    return {"provider": "deterministic", "undo_steps": deterministic}


async def synthesize_elevenlabs_tts(text: str) -> Dict[str, Any]:
    """Generate low-latency whispered audio with ElevenLabs, or signal browser SpeechSynthesis fallback."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return {"fallback_tts": True, "provider": "browser-speech-fallback", "text": text}

    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.55, "similarity_boost": 0.75},
    }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                audio_b64 = base64.b64encode(resp.content).decode("ascii")
                return {
                    "fallback_tts": False,
                    "provider": "elevenlabs-turbo-v2.5",
                    "audio_b64": audio_b64,
                    "mime_type": "audio/mpeg",
                    "text": text,
                }
    except Exception:
        pass

    return {"fallback_tts": True, "provider": "browser-speech-fallback", "text": text}


def ai_status() -> Dict[str, Any]:
    return {
        "gemini": bool(_get_gemini_key()),
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY")),
        "modal_url": os.getenv("MODAL_RECONSTRUCT_URL") or "stub-local-ready",
    }


async def answer_space_question_with_gemini(
    query: str,
    objects: List[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    """Use Gemini 2.5 Flash to match a natural-language question to scanned room objects."""
    gemini_key = _get_gemini_key()
    if not gemini_key or not query.strip() or not objects:
        return None
    compact = [
        {
            "id": o.get("id"),
            "label": o.get("label"),
            "zone": o.get("zone_name") or "Room",
            "seen_by": o.get("seen_by"),
            "position": o.get("position"),
        }
        for o in objects[:40]
    ]
    prompt = (
        "You are WorldGit on Meta Ray-Ban Display glasses. "
        "Given the user's question and the scanned 3D room objects JSON, pick the best matching object_id "
        "and write a short, natural 1-sentence spoken_answer telling the user where it is (mention the zone/area, do NOT mention meter distances). "
        'Return ONLY JSON: {"object_id": "<id or null>", "spoken_answer": "<short sentence>"}.\n\n'
        f"Question: {query}\n"
        f"Objects: {json.dumps(compact)}"
    )
    try:
        from google import genai

        client = genai.Client(api_key=gemini_key)
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        match = re.search(r"\{[\s\S]*\}", resp.text or "")
        if match:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict) and parsed.get("spoken_answer"):
                return {
                    "object_id": str(parsed.get("object_id") or ""),
                    "spoken_answer": str(parsed["spoken_answer"]),
                }
    except Exception:
        pass
    return None


async def transcribe_pcm_with_gemini(pcm_bytes: bytes, sample_rate: int = 16000) -> str:
    """Transcribe 16-bit mono PCM audio using Gemini 3.8 Flash."""
    gemini_key = _get_gemini_key()
    if not gemini_key or len(pcm_bytes) < 3200:
        return ""
    import io
    import wave
    import numpy as np

    samples = np.frombuffer(pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % 2)], dtype="<i2")
    if samples.size == 0:
        return ""
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    if rms < 110.0:
        return ""

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=gemini_key)
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=buf.getvalue(), mime_type="audio/wav"),
                "Transcribe ONLY the exact words spoken in this short voice note. "
                "Do not add quotes, prefixes, or commentary. If no clear speech is audible, return NOTHING.",
            ],
        )
        text = (resp.text or "").strip().strip('"').strip("'")
        if text.upper() == "NOTHING":
            return ""
        return text[:240]
    except Exception:
        return ""
