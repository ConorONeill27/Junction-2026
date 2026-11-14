# GPS restaurant reviews

Run from the repository root (uses existing backend FastAPI/uvicorn dependencies):

```powershell
python -m uvicorn backend.reviewer_code.server:app --host 0.0.0.0 --port 8766
```

Open http://127.0.0.1:8766 for a 600×600 preview and simulation controls.
The seed list is `places.json`: name, rating, coordinates, title, review, author.
`bar` is at 60.1567876, 24.6304562, rated five stars by Alec:
“Really liked this bar”. No API keys or external services are required.

## Meta Display simulator / Android app

The existing Lane 3 Display Access app includes `ReviewerClient.kt` from this
folder through its Gradle source set. Small integration hooks live in that app;
the YOLO camera path remains available with its normal server address.

1. Build the updated APK with `python backend/yolo_world/android-display-access/build_apk.py`.
2. Install it and open its YOLO screen. Enter `http://<laptop-IP>:8766/reviewer`
   as the server address and connect. Grant precise location permission.
   For USB, forward port 8766 with `adb reverse tcp:8766 tcp:8766` and use
   `http://127.0.0.1:8766/reviewer`.
3. Open the app's built-in Meta display simulator / preview as for the YOLO demo.
4. In the server's browser controls, enable **Use simulated pose** and Apply.
   Defaults put the wearer about 5 m south of the bar, looking north. The card
   appears on the right, stars above the review. Heading 180 hides it; moving
   latitude to 60.1566 also hides it. These controls drive the Android preview too.
5. Disable simulation for real phone GPS and glasses orientation. Connect the
   glasses session with motion enabled (the existing glasses-camera action
   attaches motion). Reviewer mode does not upload camera frames.

Live GPS must be less than 15 seconds old, with reported accuracy at most 10 m.
Glasses orientation must be less than 3 seconds old. Missing permission, GPS,
or heading clears the card and shows a waiting status. A phone compass is never
used as a substitute for head direction.

The app's existing motion stream exposes an orientation quaternion, not a
verified true-north compass reading. Before live use, align its reference with
true north using **Glasses north calibration offset** in the browser controls.
For example, while facing north, set offset to the negative raw heading.
Recalibrate after reconnecting if the reference changes. Hardware north alignment
and simulator motion availability still need device validation. GPS accuracy is
an estimate; a 10 m proximity rule cannot guarantee actual physical distance.

The gate uses haversine distance <=10 m and initial bearing within ±30 degrees
of head heading (clockwise from true north). Coincident points under 5 cm have
no defined bearing and hide the card. The nearest matching restaurant is shown.
Simulation is off by default, explicitly labeled, and settings reset on restart.

API: GET `/api/places`, GET/POST `/api/settings`, POST `/api/reviews` with
`{"latitude":60.1567426,"longitude":24.6304562,"heading":0}`.
The server binds to your LAN for phone access; use a trusted demo network.

```powershell
python -m unittest backend.reviewer_code.test_reviewer
```
