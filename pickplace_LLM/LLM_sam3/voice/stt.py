"""마이크 녹음 → Whisper STT — VoiceProcessing/STT.py 기반.

실습 코드에서 바뀐 점
- language="ko" 지정
- 무음 검사: 말소리가 없으면 Whisper 에 보내지 않는다.
  (무음/잡음을 보내면 Whisper 가 없는 문장을 지어내서 로봇이 엉뚱하게 움직일 수 있음)
- Whisper prompt(단어 힌트)는 쓰지 않는다. 무음일 때 힌트 문장을 그대로 받아 적는 환각이 확인됨.
"""
import os
import tempfile

import numpy as np
import scipy.io.wavfile as wav
import sounddevice as sd
from openai import OpenAI

import config
from voice.audio_device import resolve_input_device

FRAME_SEC = 0.03


def speech_seconds(audio, samplerate=config.SAMPLE_RATE):
    """녹음에서 말소리로 보이는 시간(초), 최대 RMS, 판정 기준 RMS"""
    x = np.asarray(audio, dtype=np.float32).flatten()
    frame = int(samplerate * FRAME_SEC)
    n = len(x) // frame
    if n == 0:
        return 0.0, 0.0, float(config.SPEECH_RMS_MIN)
    rms = np.sqrt((x[:n * frame].reshape(n, frame) ** 2).mean(axis=1))
    threshold = max(config.SPEECH_RMS_MIN, float(np.percentile(rms, 20)) * 3)
    return float((rms > threshold).sum() * FRAME_SEC), float(rms.max()), threshold


class STT:
    def __init__(self, openai_api_key, duration=config.RECORD_SECONDS):
        self.client = OpenAI(api_key=openai_api_key)
        self.duration = duration
        self.samplerate = config.SAMPLE_RATE

    def record(self):
        print(f"음성 녹음을 시작합니다. {self.duration}초 동안 말해주세요...")
        audio = sd.rec(int(self.duration * self.samplerate), samplerate=self.samplerate,
                       channels=1, dtype="int16", device=resolve_input_device())
        sd.wait()
        return audio

    def transcribe_array(self, audio, quiet=False):
        """int16 numpy 녹음 → 텍스트 (웨이크워드 확인용 짧은 클립에도 사용)"""
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            wav.write(path, self.samplerate, np.asarray(audio, dtype=np.int16).reshape(-1))
            with open(path, "rb") as f:
                text = self.client.audio.transcriptions.create(model="whisper-1", file=f, language="ko").text.strip()
            if not quiet:
                print("STT 결과:", text)
            return text
        finally:
            os.remove(path)

    def speech2text(self):
        audio = self.record()
        seconds, peak, threshold = speech_seconds(audio, self.samplerate)
        print(f"녹음 완료. 말소리 {seconds:.1f}초 (최대 RMS {peak:.0f} / 기준 {threshold:.0f})")
        if seconds < config.MIN_SPEECH_SEC:
            print("말소리가 감지되지 않아 STT 를 건너뜁니다.")
            return ""

        print("Whisper 에 전송 중...")
        return self.transcribe_array(audio)
