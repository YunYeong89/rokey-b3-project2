from pathlib import Path
import sys
import time

import cv2
import numpy as np

MODEL_DIR = Path(__file__).resolve().parents[1] / "model"
sys.path.insert(0, str(MODEL_DIR / "DINOv3"))
from group imprt GroupStream, create_parser

class DinoModel:
    def __init__(self):
        self.pipeline = GroupStream(create_parser().parse_args([]))

    def get_frame(self, img_node, duration=0.5):
        end_time = time.time() + duration
        while time.time() < end_time:
            img_node.get_color_frame()
        return img_node.get_color_frame()

    def get_best_detection(self, img_node, target):
        bgr = self.get_frame(img_node)
        if bgr is None:
            print("No color frame received.")
            return None, None

        self.pipeline.set_instruction(target)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        _, boxes, groups, scores, mask_scores = self.pipeline.select(rgb, *self.pipeline.segmenter(rgb))
        if len(boxes) == 0:
            print(f'No group matches '{target}'/')
            return None, None

        best = np.lexsort((mask_scores, scores))[-1]
        print(f''{target}': {len(np.unique(groups))} groups, {len(boxes)} matches, '
              f'best G{groups[best]} {scores[best]:.3f}')
        return boxes[best].tolist(), float(scores[best])
