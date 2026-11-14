"""Pack a folder of overlapping room photos into a single MP4 for VGGT reconstruction."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _iter_images(input_dir: Path) -> list[Path]:
    files = [p for p in sorted(input_dir.iterdir()) if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    if not files:
        raise FileNotFoundError(f"No images found in {input_dir}")
    return files


def _load_bgr_upright(path: Path, max_dim: int) -> np.ndarray:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        w, h = img.size
        if max_dim > 0 and max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            img = img.resize((max(2, int(round(w * scale))), max(2, int(round(h * scale)))), Image.Resampling.LANCZOS)
        rgb = np.asarray(img)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _fit_frame(frame: np.ndarray, target_size: Tuple[int, int]) -> np.ndarray:
    tw, th = target_size
    fh, fw = frame.shape[:2]
    if (fw, fh) == (tw, th):
        return frame
    scale = min(tw / float(fw), th / float(fh))
    nw, nh = max(2, int(round(fw * scale))), max(2, int(round(fh * scale)))
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((th, tw, 3), dtype=np.uint8)
    x0 = (tw - nw) // 2
    y0 = (th - nh) // 2
    canvas[y0 : y0 + nh, x0 : x0 + nw] = resized
    return canvas


def pack_photos_to_mp4(
    input_dir: Path,
    output_mp4: Path,
    fps: float = 2.0,
    max_dim: int = 1280,
) -> dict:
    """Write all images in input_dir (EXIF-corrected) into a single MP4 video."""
    images = _iter_images(input_dir)
    first = _load_bgr_upright(images[0], max_dim=max_dim)
    th, tw = first.shape[:2]
    # Keep even dimensions for MP4 codecs
    tw -= tw % 2
    th -= th % 2
    target_size = (max(2, tw), max(2, th))

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(output_mp4),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        target_size,
    )
    if not writer.isOpened():
        raise RuntimeError(f"Failed to open VideoWriter for {output_mp4}")

    try:
        for img_path in images:
            frame = _load_bgr_upright(img_path, max_dim=max_dim)
            writer.write(_fit_frame(frame, target_size))
    finally:
        writer.release()

    return {
        "output": str(output_mp4),
        "frame_count": len(images),
        "size": target_size,
        "fps": fps,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pack room photos into one MP4 for VGGT.")
    parser.add_argument("input_dir", type=Path, help="Folder containing overlapping room photos")
    parser.add_argument("-o", "--output", type=Path, default=Path("room_scan.mp4"), help="Output MP4 path")
    parser.add_argument("--fps", type=float, default=2.0, help="Output video FPS (default: 2.0)")
    parser.add_argument("--max-dim", type=int, default=1280, help="Max width/height in pixels (default: 1280)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    info = pack_photos_to_mp4(args.input_dir, args.output, fps=args.fps, max_dim=args.max_dim)
    print(
        f"Packed {info['frame_count']} photos -> {info['output']} "
        f"({info['size'][0]}x{info['size'][1]} @ {info['fps']} fps)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
