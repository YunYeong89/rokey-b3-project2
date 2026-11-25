import time

import cv2


class Webcam:
    def __init__(self, index=0, hz=30, width=1280, height=720):
        self.period = 1.0 / hz
        self.cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, hz)

    def read(self):
        _, frame = self.cap.read()
        return frame

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
