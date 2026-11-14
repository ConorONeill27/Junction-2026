"""Seed Demo 2 restaurant Place Anchors (works out-of-the-box or with real storefront photos)."""

from pathlib import Path
import cv2
import numpy as np

from place_anchors import DEFAULT_PLACES_DIR, PlaceAnchorStore

DEMO_RESTAURANTS = [
    ("Burger King", 1, "Worst Burger King ever :(", "Alex", (20, 60, 210)),
    ("Momotoko Ramen", 4, "Great ramen, quick service!", "Sam", (30, 140, 220)),
    ("Espresso House", 5, "Best oat latte in the mall", "Alex", (40, 110, 70)),
    ("Subway", 3, "Solid tuna melt, long line at lunch", "Sam", (20, 155, 60)),
]


def make_storefront_card(title: str, accent_bgr: tuple[int, int, int]) -> np.ndarray:
    """Create a rich, textured storefront banner so ORB homography matches cleanly in tests/demos."""
    img = np.zeros((240, 360, 3), dtype=np.uint8)
    img[:, :] = (32, 36, 44)
    cv2.rectangle(img, (12, 12), (348, 228), accent_bgr, 4)
    cv2.rectangle(img, (24, 24), (336, 95), accent_bgr, -1)
    cv2.putText(img, title.upper(), (34, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2, cv2.LINE_AA)
    # Add distinctive geometric storefront markers for reliable ORB keypoints
    rng = np.random.default_rng(abs(hash(title)) % (2**32))
    for _ in range(45):
        x = int(rng.integers(28, 330))
        y = int(rng.integers(108, 216))
        r = int(rng.integers(4, 14))
        col = tuple(int(c) for c in rng.integers(90, 255, size=3))
        cv2.circle(img, (x, y), r, col, 2)
    return img


def seed_places(places_dir: Path = DEFAULT_PLACES_DIR, reset: bool = False) -> list[dict]:
    store = PlaceAnchorStore(places_dir)
    if reset:
        store.clear()
    existing = {p["name"] for p in store.list_places()}
    seeded = []
    for name, rating, review, author, color in DEMO_RESTAURANTS:
        if name in existing:
            continue
        rec = store.add_place(name, rating, review, author=author, image_bgr=make_storefront_card(name, color))
        seeded.append(rec)
    return seeded


if __name__ == "__main__":
    added = seed_places(reset=True)
    print(f"Seeded {len(added)} demo restaurant Place Anchors into {DEFAULT_PLACES_DIR}")
