# YOLO-World phone camera and Meta Display Access

Adapted from Meta's official Android DisplayAccess sample (upstream LICENSE preserved):
https://github.com/facebook/meta-wearables-dat-android/tree/main/samples/DisplayAccess
Requires Android 12+ and a rear camera.

## Run

1. On the laptop, run `backend/yolo_world/start.cmd` (or `python backend/yolo_world/run.py --source phone --headless`). The default source is now `phone`; no laptop camera is opened.
2. Install `app/build/outputs/apk/debug/app-debug.apk` on the phone and grant camera permission.
3. For USB, enable USB debugging, approve the laptop, and run from this folder:

```powershell
.tools/sdk/platform-tools/adb.exe install -r app/build/outputs/apk/debug/app-debug.apk
.tools/sdk/platform-tools/adb.exe reverse tcp:8765 tcp:8765
```

Open Display Access. Use `http://127.0.0.1:8765`, then **Connect / camera**.
Repeat USB forwarding after reconnecting. For Wi-Fi, use `http://<laptop-LAN-IP>:8765`
and allow the Python process through the private-network firewall.
For USB-only use, launch the runner with `--host 127.0.0.1`.

The phone shows a portrait rear-camera feed and small outlined object-name labels
at the detected centers. Frames are rotated upright before upload; label placement
uses the same aspect-preserving fit as the preview. Keep the phone upright.
Camera2 capture requests 60 FPS (the nearest supported camera range is selected).
Actual capture depends on the device. Tracking uploads target every 16.7 ms with
one JPEG request in flight; frames are dropped under network or processing load.
YOLO runs independently in one worker at 0.2-second start intervals (5 FPS) (or slower if
inference exceeds that interval). Completed detections are advanced through a
bounded frame history before use, avoiding jumps to old camera coordinates.
Sparse OpenCV Lucas-Kanade flow moves each box between detections; forward/backward
checks and RANSAC reject unreliable tracks, which disappear until redetected.
The default `--device auto` uses CUDA for YOLO when available; `--device 0` explicitly
selects the RTX GPU. OpenCV flow currently runs on CPU: the installed OpenCV wheel
does not include CUDA. This is a 60 FPS target, not a guaranteed display refresh rate.

Phone `TYPE_GAME_ROTATION_VECTOR` orientation supplies the camera-motion prior
in this simulation. A simulator can call `YoloViewModel.setSimulatedCameraRotation`
with a row-major 3x3 camera-to-world rotation (or null to return to the phone sensor).
The JPEG and pose must use matching camera axes: right, down, forward. Keep the
phone portrait; the default conversion assumes the upright rear-camera JPEG.
The laptop accepts this pose via the optional `X-Camera-Rotation` header and uses
`K R_current^T R_previous K^-1` to initialize LK feature positions with
`OPTFLOW_USE_INITIAL_FLOW`. Without sensor data, visual tracking still works.
Intrinsics are approximate; real glasses need calibrated camera intrinsics and
camera/IMU alignment. Accelerometer translation is intentionally not integrated
without depth and bias calibration. No real glasses IMU API is wired in this sample.

Verify tracking and the JPEG endpoint without hardware:
`python -m unittest discover -s backend/yolo_world -p "test_*.py" -v`.

## Meta developer glasses simulation

Tap **Meta preview**, then **Developer preview**, choose **In app**, and start preview.
Open the YOLO sample (the former Car Maintenance sample). It renders a transparent
600x600 image containing the same compact, center-positioned labels. The phone
camera keeps capturing while navigating inside the app, including developer preview.
The Meta renderer receives overlay images; the camera feed remains on the phone.
A square HUD letterboxes the portrait camera coordinate space to preserve alignment.
This is a phone-camera simulation; real glasses require camera/display calibration.
Physical glasses still use the sample registration and display-session setup.

Camera access stops when the app goes into the background. Failed requests clear
labels and detections older than three seconds are removed. No keys or inference
runtime are needed on the phone. Inference runs entirely on the laptop.

## Build

```powershell
python backend/yolo_world/android-display-access/build_apk.py
```

Portable build tools and the APK are ignored by Git. Optional Meta credentials
belong in ignored `local.properties`. You can also open this folder in Android Studio.

## Local API

`POST /api/frame` on port 8765 accepts an upright JPEG (maximum 2 MB), runs YOLO,
and returns `timestamp`, `source: phone_camera`, `frame_size_px: [width, height]`,
`detections` (including `label`, `bbox_xyxy`, `center_px`, confidence and object ID),
and `inference_seconds`. Requests while inference is busy return 429.
`GET /api/detections` remains available for the latest snapshot.
The separate FastAPI API and WebSocket contracts are unchanged.

To retain laptop webcam mode: `python backend/yolo_world/run.py --source 0`.
The existing TXT centers and object crops are still refreshed after inference.
The hardware-free sample server remains available as `python backend/yolo_world/detection_server.py`;
the phone camera upload mode needs the actual runner instead.

Run API tests with:
`python -m unittest discover -s backend/yolo_world -p test_phone_frames.py -v`.

The app chooses the supported FPS range closest to fixed 60 FPS, falling back to
supported rates on older cameras. Status reports the selected range and measured
camera/flow response rates separately. A 60 FPS request is not a display guarantee.
Continuous-video autofocus is enabled when supported, then continuous-picture,
then a triggered autofocus scan for auto-only cameras. Fixed-focus cameras
keep their existing mode. Flow runs on images reduced to a 320-pixel maximum side;
coordinates are converted back to full frame size. All boxes share the same LK
image pyramid calculation to keep processing inside the 16.7 ms target budget.

Camera2 is used because this phone exposes only 30 FPS through the legacy Camera API,
while Camera2 advertises fixed 60 FPS. Camera2 uses a YUV ImageReader with two
buffers and drops old frames rather than accumulating latency.

## Pinch-to-record audio

The YOLO glasses HUD keeps a dedicated **Record audio / Stop recording** control,
separate from the primary cursor action. Select the audio control to record, then
select **Stop recording** to save. The phone camera screen also retains its audio
and retry controls. In **Meta preview > Developer preview > In app**, use the same
audio control in the SDK simulated display. The simulator uses the phone
microphone, and requests Android microphone permission on first use. Physical
Neural Band hardware and a glasses microphone route have not been verified.

Clips are AAC audio in M4A files, saved on the laptop in
`backend/yolo_world/audioClips/clip_<uuid>.m4a`. The app posts binary `audio/mp4`
to `POST /api/audio-clips` on the same laptop address/USB port 8765 as YOLO.
Recordings stop automatically when the app goes into the background, or after
10 minutes. Very short/invalid recordings show an error instead of uploading.

A temporary phone copy in the app's private `pendingAudioClips` directory survives
an interrupted upload. On the phone's camera screen, **Retry upload** sends pending
clips to the current laptop address. A successful upload removes the phone copy.
Retries use the clip UUID and do not create duplicate laptop files. No cloud service
or API key is involved. Generated `audioClips/` content is ignored by Git.

Run `python -m unittest discover -s backend/yolo_world -p "test_*.py" -v` to verify
uploads, idempotence, invalid input, and saving while inference is busy. Build and
install the APK, then test both recording transitions in the in-app display preview;
confirm `WorldGitAudio` logs report positive duration and a saved laptop path.

## Head-aimed cursor and object selection

The primary Neural Band pinch action now controls the cursor; audio recording
remains a separate explicit control. First pinch: show a small white circle at the
center and set the current head pose as neutral. Move your head to aim. The next
pinch selects the text label under the circle and fills that label blue. Pinching
empty space clears selection and hides the circle. Pinching again shows a newly
centered cursor. Selection follows the tracked object ID between camera frames.

The mobile simulator uses the existing phone rotation-vector sensor as the head
pose proxy. Move/tilt the phone to simulate head aiming. Its **Pinch: show cursor /
select / hide** button exercises the same action as the display's primary button.
The existing `setSimulatedCameraRotation` entry point also feeds head aiming.
No touch-drag movement is used. Real glasses head-pose input is not verified.

Head motion is relative to the pose at activation, with a 60-degree horizontal and
45-degree vertical aiming span. Cursor positions, label drawing, and hit testing
share camera-image coordinates, including letterboxing, on both phone and HUD.
Leaving the camera view/app hides and resets the cursor. Rotation-vector updates
commit cursor state at most 30 times per second.

Head-aiming unit tests: `gradlew.bat testDebugUnitTest` (from the Android directory,
with the same JAVA_HOME/ANDROID_HOME/GRADLE_USER_HOME used by `build_apk.py`).
