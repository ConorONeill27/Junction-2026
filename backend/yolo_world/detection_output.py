"""Write detection centers and bounding boxes as one replaceable text snapshot."""

import os
from pathlib import Path
import tempfile

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "detections.txt"


def write_detection_centers(detections, output_path=DEFAULT_OUTPUT):
    """Pixel origin is top-left; x increases rightward, y increases downward."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["label\tcenter_x_px\tcenter_y_px\tconfidence\tx1_px\ty1_px\tx2_px\ty2_px\tobject_id"]
    for detection in detections:
        x1, y1, x2, y2 = detection["bbox_xyxy"]
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        label = str(detection["label"]).replace("\t", " ").replace("\n", " ").replace("\r", " ")
        lines.append(
            f"{label}\t{center_x:.2f}\t{center_y:.2f}\t{detection['confidence']:.4f}"
            f"\t{x1:.2f}\t{y1:.2f}\t{x2:.2f}\t{y2:.2f}\t{detection.get('object_id', '')}"
        )
    if not detections:
        lines.append("# No objects detected")
    # Replace once per batch, so readers never see a partly written snapshot.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_path.parent,
                                         suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write("\n".join(lines) + "\n")
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
