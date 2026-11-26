"""RealSense 컬러 프레임에서 group.py 방식(분할 → DINOv3 그룹 → CLIP instruction)으로 물품 마스크를 고르고,
컬러 시점에 정렬한 depth로 가까운 물체부터 0, 1, 2 … 라벨을 각 물체 상단에 표시합니다."""

from pathlib import Path

import cv2
import numpy as np

from group import DINO_DIR, GroupStream, add_group_arguments
from instruction import create_parser as create_clip_parser

FONT = cv2.FONT_HERSHEY_SIMPLEX


class RealSenseCamera:
    """RealSense 컬러(BGR)와 컬러에 정렬한 depth(m)를 FrameShm과 같은 read/close/frame 인터페이스로 제공합니다."""

    def __init__(self, width, height, fps):
        import pyrealsense2 as rs  # 카메라를 직접 열 때만 필요해 ROS(토픽 입력) 환경에는 설치하지 않아도 됩니다.
        self.pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
        config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
        profile = self.pipeline.start(config)
        self.scale = profile.get_device().first_depth_sensor().get_depth_scale()  # z16 단위 → m
        self.align = rs.align(rs.stream.color)
        self.frame = np.zeros((height, width, 3), dtype=np.uint8)  # InstructionStream.run 대기 화면 크기

    def read(self, timeout=None):
        """(frame_id, stamp_ns, (BGR (H, W, 3), depth m (H, W)))를 반환합니다. depth 0은 측정 실패 픽셀입니다."""
        ok, frames = self.pipeline.try_wait_for_frames(round((timeout or 5) * 1000))
        if not ok:
            raise TimeoutError("No RealSense frame available.")
        frames = self.align.process(frames)
        color, depth = frames.get_color_frame(), frames.get_depth_frame()
        bgr = np.asanyarray(color.get_data()).copy()
        meters = np.asanyarray(depth.get_data()).astype(np.float32) * self.scale
        return color.get_frame_number(), round(color.get_timestamp() * 1e6), (bgr, meters)

    def capture(self, warmup=30):
        """자동 노출이 안정되도록 warmup 프레임을 버린 뒤 (BGR, depth m) 한 장을 반환합니다."""
        for _ in range(warmup):
            self.read()
        return self.read()[2]

    def close(self):
        self.pipeline.stop()


class DepthRanker:
    """마스크 안 유효 depth의 백분위수를 물체 거리로 보고 가까운 순서를 매깁니다.
    마스크 경계에는 뒤쪽 배경 depth가 섞이므로 중앙값(50)을, 앞면 기준으로 보려면 더 낮은 값을 씁니다."""

    def __init__(self, percentile):
        self.percentile = percentile

    def __call__(self, depth, masks):
        """depth (H, W) m, 마스크 (N, H, W) → 가까운 순 인덱스 (M,)와 거리 m (M,). 유효 depth가 없는 마스크는 뺍니다."""
        z = np.array([np.percentile(depth[mask], self.percentile) if mask.any() else np.nan
                      for mask in masks & (depth > 0)])
        order = np.argsort(z)[:np.isfinite(z).sum()]  # nan은 argsort 맨 뒤
        return order, z[order]


class GraspStream(GroupStream):
    """RealSense (BGR, depth) → group.py 선택 → depth 가까운 순 0부터 라벨. 스트림 구동은 InstructionStream.run을 그대로 씁니다."""

    def __init__(self, args):
        super().__init__(args)
        self.ranker = DepthRanker(args.depth_percentile)
        self.name += " + depth"

    def open_source(self):
        print(f"Source: RealSense {self.args.width}x{self.args.height} @ {self.args.fps} fps.", flush=True)
        return RealSenseCamera(self.args.width, self.args.height, self.args.fps)

    def rank(self, frame):
        """(BGR, depth m) → instruction 물품을 가까운 순으로 정렬한 (마스크, 박스, 그룹 ID, 거리 m)와 전체 마스크 수."""
        bgr, depth = frame
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        masks, boxes = self.segmenter(rgb)
        hit_masks, hit_boxes, groups, _, _ = self.select(rgb, masks, boxes)
        order, z = self.ranker(depth, hit_masks)
        return hit_masks[order], hit_boxes[order], groups[order], z, len(masks)

    def process(self, frame):
        """(BGR, depth m) → 가까운 순 라벨을 그린 (overlay, 상태 문자열)."""
        masks, boxes, groups, z, total = self.rank(frame)
        overlay = self.render(frame[0], masks, boxes, groups, z)
        ranks = " ".join(f"{rank}:{dist:.2f}m" for rank, dist in enumerate(z))
        return overlay, f"Groups: {len(np.unique(groups))} | Grasp {len(z)}/{total} | {ranks}"

    @classmethod
    def render(cls, bgr, masks, boxes, groups, depths):
        """가까운 순으로 정렬된 마스크를 그룹 색으로 칠하고 박스 상단 중앙에 '<순번> <거리>m'를 그립니다.
        라벨이 겹치면 0이 맨 위에 보이도록 먼 것부터 그립니다."""
        overlay, colors = cls.paint(bgr, masks, groups)
        for rank in reversed(range(len(masks))):
            x1, y1, x2, _ = boxes[rank].astype(int)
            text = f"{rank} {depths[rank]:.2f}m"
            (w, h), base = cv2.getTextSize(text, FONT, 0.6, 2)
            x, y = (x1 + x2 - w) // 2, max(y1 - base - 4, h + 4)
            cv2.rectangle(overlay, (x - 4, y - h - 4), (x + w + 4, y + base + 2), colors[rank].tolist(), -1)
            cv2.putText(overlay, text, (x, y), FONT, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
        return overlay


def create_parser():
    parser = add_group_arguments(create_clip_parser(
        "Select instruction items like group.py on RealSense color frames, then label them 0, 1, 2, ... from the "
        "nearest by aligned depth. Streams to a window, or saves one frame with --output."))
    parser.add_argument("--width", type=int, default=640, help="RealSense color/depth width.")
    parser.add_argument("--height", type=int, default=480, help="RealSense color/depth height.")
    parser.add_argument("--fps", type=int, default=30, help="RealSense color/depth frame rate.")
    parser.add_argument("--depth-percentile", type=float, default=50,
                        help="Percentile of valid depth inside each mask used as its distance (lower = front surface).")
    parser.add_argument("--output", type=Path, nargs="?", const=DINO_DIR / "results" / "grasp.jpg",
                        help="Save one processed frame instead of streaming (no path: results/grasp.jpg).")
    return parser


def parse_args():
    parser = create_parser()
    args = parser.parse_args()
    if args.output and not args.instruction:
        parser.error("--instruction is required with --output, e.g. --instruction can.")
    return args


def main():
    args = parse_args()
    pipeline = GraspStream(args)
    if args.output is None:
        pipeline.run()
        return
    camera = pipeline.open_source()
    frame = camera.capture()
    camera.close()
    pipeline.set_instruction(args.instruction)
    overlay, status = pipeline.process(frame)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.output), overlay)
    print(f"Saved {args.output} | {args.instruction} | {status}")


if __name__ == "__main__":
    main()
