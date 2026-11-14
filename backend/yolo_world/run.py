"""Standalone YOLO-World camera detection; no FastAPI startup dependencies."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import sys
import time

from detection_output import DEFAULT_OUTPUT, write_detection_centers
from object_store import DEFAULT_OBJECTS_DIR, ObjectStore
from detection_server import DetectionServer

CAMERA_SOURCE = 0  # Laptop webcam; change to another index, video path, or RTSP URL.
INFERENCE_INTERVAL = 0.2  # 5 YOLO detection passes per second.
MODEL_PATH = Path(__file__).resolve().parent / "yolov8s-worldv2.pt"


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("interval must be a finite positive number")
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.getenv("YOLO_CAMERA_SOURCE", "phone"))
    parser.add_argument("--interval", type=positive_seconds, default=INFERENCE_INTERVAL)
    parser.add_argument("--model", type=Path, default=MODEL_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Latest detection centers TXT file (overwritten each pass)")
    parser.add_argument("--objects-dir", type=Path, default=DEFAULT_OBJECTS_DIR, help="Folder containing object IDs and crops")
    parser.add_argument("--host", default="0.0.0.0", help="Detection API bind address")
    parser.add_argument("--port", type=int, default=8765, help="Phone app detection API port")
    parser.add_argument("--device", default="auto", help="auto selects CUDA when available; cpu or 0 also supported")
    parser.add_argument("--classes", nargs="+", help="Optional custom vocabulary; first use downloads a text encoder")
    parser.add_argument("--headless", action="store_true", help="JSON output without a preview window")
    parser.add_argument("--max-detections", type=int, default=0, help="Stop after N inference passes; 0 runs until interrupted")
    args = parser.parse_args()
    if args.max_detections < 0:
        parser.error("--max-detections must be zero or positive")

    os.environ.setdefault("YOLO_CONFIG_DIR", str(MODEL_PATH.parent / "settings"))
    try:
        import cv2
        import torch
        from ultralytics import YOLOWorld
        cv2.setNumThreads(4)
    except ImportError as exc:
        print(f"Missing dependency: {exc}. Install yolo_world/requirements.txt.", file=sys.stderr)
        return 1

    if args.device == "auto":
        args.device = "0" if torch.cuda.is_available() else "cpu"
    print(f"Inference device: {args.device}", file=sys.stderr, flush=True)

    capture = None
    executor = None
    detection_server = None
    tracking = None
    window = "WorldGit YOLO-World | Q or Esc to quit"
    try:
        # Loaded only when explicitly launched. First run downloads the weights.
        print(f"Loading YOLO-World: {args.model}", file=sys.stderr, flush=True)
        model = YOLOWorld(str(args.model))
        model.to('cpu' if args.device == 'cpu' else f'cuda:{args.device}' if str(args.device).isdecimal() else args.device)
        object_store = ObjectStore(args.objects_dir)
        def predict_phone(frame):
            result = model.predict(frame, device=args.device, verbose=False)[0]
            detections = [{"label": result.names[int(box.cls.item())],
                           "confidence": round(float(box.conf.item()), 4),
                           "bbox_xyxy": box.xyxy[0].cpu().tolist()} for box in result.boxes]
            for item in detections:
                x1, y1, x2, y2 = item["bbox_xyxy"]
                item["center_px"] = [(x1 + x2) / 2, (y1 + y2) / 2]
            object_store.update(detections, frame)
            write_detection_centers(detections, args.output)
            return detections

        from flow_tracker import TrackingPipeline
        tracking = TrackingPipeline(predict_phone, args.interval)
        if args.classes:
            model.set_classes(args.classes)
        detection_server = DetectionServer(args.host, args.port, track=tracking).start()
        print(f"Phone detection API: http://<laptop-ip>:{args.port}/api/detections", flush=True)
        if args.source == "phone":
            print(f"Waiting for phone camera JPEGs at /api/frame; YOLO target {1 / args.interval:g} FPS (every {args.interval:g}s).", flush=True)
            while True:
                time.sleep(0.1)
        source = int(args.source) if args.source.isdecimal() else args.source
        print("Opening camera feed...", file=sys.stderr, flush=True)
        capture = cv2.VideoCapture(source)
        capture.set(cv2.CAP_PROP_FPS, 30)
        if not capture.isOpened():
            raise RuntimeError("Cannot open camera source. Check camera permissions and close other camera apps.")
        next_inference = 0.0
        passes = 0
        detections = []
        pending = None
        executor = ThreadPoolExecutor(max_workers=1)
        if not args.headless:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window, 1100, 750)
        print(f"Detecting every {args.interval}s; Ctrl+C to stop.", file=sys.stderr, flush=True)
        while True:
            # Drain the feed continuously so each inference sees a current frame.
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("Camera feed ended or failed to return a frame.")
            now = time.monotonic()
            if pending is not None and pending.done():
                result = pending.result()[0]
                pending = None
                detections = [
                    {"label": result.names[int(box.cls.item())],
                     "confidence": round(float(box.conf.item()), 4),
                     "bbox_xyxy": box.xyxy[0].cpu().tolist()}
                    for box in result.boxes
                ]
                object_store.update(detections, result.orig_img)
                write_detection_centers(detections, args.output)
                detection_server.publish(detections, [result.orig_img.shape[1], result.orig_img.shape[0]], str(source))
                print(json.dumps({"timestamp": time.time(), "inference_seconds": round(time.monotonic() - started, 3),
                                  "detections": detections}), flush=True)
                # Skip missed slots rather than queueing stale work on slow CPUs.
                finished = time.monotonic()
                next_inference = started + max(1, math.ceil((finished - started) / args.interval)) * args.interval
                passes += 1
                if args.max_detections and passes >= args.max_detections:
                    break
            if pending is None and now >= next_inference:
                started = time.monotonic()
                pending = executor.submit(model.predict, frame.copy(), device=args.device, verbose=False)
            if not args.headless:
                for detection in detections:
                    x1, y1, x2, y2 = map(int, detection["bbox_xyxy"])
                    label = f"{detection['label']} {detection['confidence']:.0%} #{detection['object_id'][:8]}"
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 100), 2)
                    cv2.putText(frame, label, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 100), 2)
                status = f"YOLO-World | every {args.interval}s | {len(detections)} objects"
                if pending is not None:
                    status += " | detecting..."
                cv2.rectangle(frame, (0, 0), (frame.shape[1], 35), (25, 25, 25), -1)
                cv2.putText(frame, status, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                cv2.imshow(window, frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
                if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    break
            else:
                time.sleep(0.005)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"YOLO-World stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        if capture is not None:
            capture.release()
        cv2.destroyAllWindows()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        if detection_server is not None:
            detection_server.close()
        if tracking is not None:
            tracking.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
