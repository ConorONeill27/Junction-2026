"""Frame-rate sparse LK tracking with an optional camera-to-world rotation prior."""
import copy
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np


class BoxTracker:
    def __init__(self):
        self.gray = None
        self.rotation = None
        self.items = []
        self.frame_shape = None

    @staticmethod
    def grayscale(frame):
        scale = min(1., 320. / max(frame.shape[:2]))
        small = cv2.resize(frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else frame
        return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    def reset(self, frame, rotation, items):
        self.gray = self.grayscale(frame)
        self.frame_shape = frame.shape[:2]
        self.rotation = rotation
        self.items = copy.deepcopy(items)

    def update(self, frame, rotation):
        gray = self.grayscale(frame)
        if self.gray is None or frame.shape[:2] != self.frame_shape:
            self.reset(frame, rotation, [])
            return []
        h, w = gray.shape
        # Approximate intrinsics; supply calibrated values for production hardware.
        focal = max(w, h) * 0.9
        k = np.array([[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1.]])
        homography = np.eye(3)
        if rotation is not None and self.rotation is not None:
            homography = k @ rotation.T @ self.rotation @ np.linalg.inv(k)
        scale = np.array([w / frame.shape[1], h / frame.shape[0]], dtype=np.float32)
        groups, all_points = [], []
        count = 0
        for item in self.items:
            box = np.array(item['bbox_xyxy']).reshape(2, 2) * scale
            (x1, y1), (x2, y2) = box
            left, top = max(0, int(x1)), max(0, int(y1))
            right, bottom = min(w, int(x2)), min(h, int(y2))
            if right - left < 3 or bottom - top < 3:
                continue
            points = cv2.goodFeaturesToTrack(self.gray[top:bottom, left:right], 16, 0.02, 3)
            if points is None or len(points) < 4:
                continue
            points += np.float32([left, top])
            groups.append((item, box, slice(count, count + len(points))))
            count += len(points)
            all_points.append(points)
        tracked = []
        if all_points:
            # One image pyramid pair for all objects instead of rebuilding per box.
            points = np.concatenate(all_points)
            guess = cv2.perspectiveTransform(points, homography).astype(np.float32)
            options = dict(winSize=(15, 15), maxLevel=2,
                           criteria=(cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 12, 0.03))
            moved, status, _ = cv2.calcOpticalFlowPyrLK(
                self.gray, gray, points, guess, **options,
                flags=cv2.OPTFLOW_USE_INITIAL_FLOW)
            back, back_status, _ = cv2.calcOpticalFlowPyrLK(gray, self.gray, moved, None, **options)
            valid = (status.ravel() == 1) & (back_status.ravel() == 1)
            valid &= np.linalg.norm(back - points, axis=2).ravel() < 1.5
            for item, box, indices in groups:
                good = valid[indices]
                if good.sum() < 4:
                    continue
                transform, inliers = cv2.estimateAffinePartial2D(points[indices][good], moved[indices][good],
                                                               method=cv2.RANSAC, ransacReprojThreshold=2,
                                                               maxIters=100, confidence=0.95, refineIters=3)
                if transform is None or inliers.sum() < 4:
                    continue
                (x1, y1), (x2, y2) = box
                corners = np.float32([[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]])
                corners = cv2.transform(corners, transform)[0]
                lo, hi = corners.min(axis=0), corners.max(axis=0)
                lo = np.maximum(lo, [0, 0]); hi = np.minimum(hi, [w, h])
                if np.any((hi - lo) / scale < 2):
                    continue
                lo, hi = lo / scale, hi / scale
                item['bbox_xyxy'] = [float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])]
                item['center_px'] = ((lo + hi) / 2).tolist()
                tracked.append(item)
        self.gray, self.rotation, self.items = gray, rotation, tracked
        return copy.deepcopy(tracked)


class TrackingPipeline:
    """One detector worker, bounded frame history, no queued inference requests."""
    def __init__(self, predict, interval=1.5):
        self.predict, self.interval = predict, interval
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.next_detection = 0.
        self.history = deque(maxlen=180)
        self.tracker = BoxTracker()
        self.last_frame = 0.

    def __call__(self, frame, rotation=None):
        now = time.monotonic()
        if now - self.last_frame > 2 or (self.tracker.gray is not None and self.tracker.frame_shape != frame.shape[:2]):
            self.history.clear()
            self.tracker.reset(frame, rotation, [])
        self.last_frame = now
        self.history.append((now, frame.copy(), rotation))
        if self.pending is not None and self.pending.done():
            try:
                items = self.pending.result()
                frames = list(self.history)
                # Never apply old detector coordinates directly to the current image.
                if frames and frames[0][0] <= self.started:
                    seed = next(i for i, entry in enumerate(frames) if entry[0] == self.started)
                    _, image, pose = frames[seed]
                    self.tracker.reset(image, pose, items)
                    for _, image, pose in frames[seed + 1:-1]:
                        self.tracker.update(image, pose)
            finally:
                self.pending = None
        result = self.tracker.update(frame, rotation)
        if self.pending is None and now >= self.next_detection:
            self.started = now
            self.next_detection = now + self.interval
            self.pending = self.executor.submit(self.predict, frame.copy())
        return result

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
