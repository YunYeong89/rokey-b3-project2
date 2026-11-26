from pathlib import Path
import sys
import time

import cv2


def camera_names():
    """Device names in OpenCV camera-index order."""
    if sys.platform == "darwin":  # OpenCV AVFoundation 백엔드와 같은 순서: uniqueID 정렬
        from AVFoundation import AVCaptureDevice, AVMediaTypeMuxed, AVMediaTypeVideo
        devices = [d for media in (AVMediaTypeVideo, AVMediaTypeMuxed)
                   for d in AVCaptureDevice.devicesWithMediaType_(media)]
        return [d.localizedName() for d in sorted(devices, key=lambda d: d.uniqueID())]
    nodes = {int(p.name[5:]): (p / "name").read_text().strip()
             for p in Path("/sys/class/video4linux").glob("video*")}
    return [nodes.get(i, "") for i in range(max(nodes, default=-1) + 1)]


class Webcam:
    def __init__(self, camera="C270", hz=30, width=1280, height=720):
        """camera: OpenCV index (int) or a substring of the device name (str)."""
        if isinstance(camera, str):
            names = camera_names()
            matches = [i for i, name in enumerate(names) if camera.lower() in name.lower()]
            if not matches:
                raise RuntimeError(f"Camera '{camera}' not found: {names}")
            camera = matches[0]
        self.period = 1.0 / hz
        if sys.platform == "darwin":
            self.cap = cv2.VideoCapture(camera, cv2.CAP_AVFOUNDATION)
        else:
            self.cap = cv2.VideoCapture(camera, cv2.CAP_V4L2)
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # YUYV caps 720p at ~10fps
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, hz)

    def read(self, timeout=1.0):
        """BGR 프레임 반환. 일시적으로 놓친 프레임은 timeout 동안 다시 읽습니다."""
        deadline = time.monotonic() + timeout
        while True:
            ok, frame = self.cap.read()
            if ok:
                return frame
            if time.monotonic() > deadline:
                raise RuntimeError("Camera frame read failed. Check the connection or whether another app is using it.")

    def run(self):
        while True:
            start = time.perf_counter()
            cv2.imshow("webcam", self.read())
            wait_ms = max(1, int((self.period - (time.perf_counter() - start)) * 1000))
            if cv2.waitKey(wait_ms) & 0xFF == ord("q"):
                break
        self.close()

    def close(self):
        self.cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    Webcam().run()
