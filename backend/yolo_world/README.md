# Phone-camera mode (default)

Run `start.cmd` on the laptop, then open the updated Display Access Android app.
YOLO runs on the laptop; the phone sends upright rear-camera JPEGs and displays
small labels at returned object centers. Camera capture and tracking uploads target 60 FPS; YOLO detection runs every 1.5 seconds,
with one request in flight and no frame queue.
See [Android setup and Meta developer preview](android-display-access/WORLDGIT.md).
For the previous laptop webcam behavior, pass `--source 0`.
The documentation below describes that legacy webcam mode; its old 1.5-second
interval can be selected explicitly with `--interval 1.5`.

# YOLO-World camera runner

Double-click `start.cmd` to open the camera application using system Python 3.12 (no virtual environment). Press Q, Escape, or close the window to stop. For the first installation, or to launch from `backend/` in PowerShell:

```powershell
py -3.12 -m pip install -r yolo_world/requirements.txt
py -3.12 yolo_world/run.py
```

Uses laptop camera `0`, the small `yolov8s-worldv2.pt` model, CPU inference,
and a 1.5-second start-to-start inference interval. If inference takes longer,
missed intervals are skipped. Frames are continuously read between detections
to avoid camera buffering. A resizable desktop window shows the live camera with the latest detection boxes, labels, and confidence. Inference runs in a background thread so the window stays responsive. Boxes update every inference pass and can lag moving objects between passes.
Press Q, Escape, or Ctrl+C to stop. Each pass prints labels, confidence, and pixel
bounding boxes as JSON. This standalone process does not change the Spatial Hub API.

Change `CAMERA_SOURCE` in `run.py`, set `YOLO_CAMERA_SOURCE`, or pass `--source`:

```powershell
py -3.12 yolo_world/run.py --source 1 --interval 1.5
py -3.12 yolo_world/run.py --source "rtsp://camera-address/stream" --headless
py -3.12 yolo_world/run.py --classes person laptop "coffee mug"
py -3.12 yolo_world/run.py --headless --max-detections 3
```

First use needs internet to download weights; later runs reuse the local weights.
The default uses the pretrained vocabulary. Custom `--classes` may additionally
download a text encoder and its dependencies on first use. Keep those caches for
offline use. Missing dependencies, weights, or cameras produce a clear error and
never affect FastAPI startup. Model weights/settings are ignored by Git.

Model documentation: https://docs.ultralytics.com/models/yolo-world/

Each completed detection pass replaces `detections.txt` in this folder with all
current detections, rather than appending a history or writing once per object.
`detection_output.py` computes each box center as `(x1 + x2) / 2, (y1 + y2) / 2`.
Coordinates are in original camera-frame pixels, with `(0, 0)` at the top-left,
x increasing rightward and y downward. The tab-separated columns are:

```text
label   center_x_px  center_y_px  confidence  x1_px   y1_px   x2_px   y2_px
person  320.00       240.00       0.9200      220.00  100.00  420.00  380.00
```

Bounding boxes use `(x1_px, y1_px)` for the top-left corner and
`(x2_px, y2_px)` for the bottom-right corner, in original camera-frame pixels.

Updates follow the 1.5-second inference schedule; slow inference delays updates.
An empty detection pass replaces old objects with `# No objects detected`.
Use `--output path/to/results.txt` to choose another output file. Restart a
running application after updating the code to enable the new output.

Each object is saved under `objects/<object_id>/image.jpg`, cropped from the
original inference frame (without drawn overlays). `metadata.json` contains its
label, confidence, bounding box, center, frame dimensions, and first/last-seen
timestamps. Each detection pass refreshes the crop and metadata and deletes
generated files and empty folders for objects absent from that pass. An empty
pass clears all saved object crops. The TXT has an additional final `object_id` column linking
each detection to its folder, and the preview shows an abbreviated ID.

IDs use same-label bounding-box overlap (at least 30% IoU), with a 10-second
matching window. This is approximate tracking: fast movement, crossing objects,
long absences, or restarting the app can assign a new ID. Stale folders from
previous runs are cleaned on the first completed pass. Use `--objects-dir path/to/objects` to relocate
the crops. Restart `start.cmd` to enable these changes.

## Android Display Access app

See [android-display-access/WORLDGIT.md](android-display-access/WORLDGIT.md) for
the adapted official Meta sample, APK build instructions, USB/Wi-Fi connection,
and the built-in glasses display preview. The runner now serves the latest batch
at `http://<laptop-ip>:8765/api/detections`. Use `--host 127.0.0.1` for USB-only
access or change `--port` as needed. Inference stays on the laptop; the phone and
glasses display the resulting object list.
