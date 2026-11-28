"""웨이크워드 감지 — "자비스" 한 단어로 호출. (VoiceProcessing/wakeup_word.py 기반)

openwakeword 기본 모델(hey_jarvis)은 영어로 학습돼서 한국어 발음 "자비스" 에는 반응하지 않는다(실측 0.00).
그래서 두 가지를 동시에 쓴다 (config.WAKEWORD_MODE = "hybrid"):
  1) 말소리 구간 + Whisper : 짧은 말소리가 끝나면 Whisper 로 받아 적고, "자비스/Jarvis" 가 있으면 깨어난다.
                             한국식("자비스")·영어식("Jarvis")·중간 발음 모두 OK — 판정은 wake_match.py 의 발음 규칙. 말소리가 있을 때만 API 호출(짧은 클립이라 비용 매우 적음)
  2) hey_jarvis 모델        : 영어식 "Jarvis" / "Hey Jarvis" 는 로컬에서 바로 감지

"자비스, 라면 하나 추가해줘" 처럼 이어 말하면 wait() 가 뒷부분("라면 하나 추가해줘")을 돌려줘서
따로 다시 녹음하지 않고 바로 명령으로 쓴다.
"""
import queue
from collections import deque

import numpy as np
import openwakeword
import sounddevice as sd
from openwakeword.model import Model

import config
from voice.audio_device import resolve_input_device
from voice.wake_match import find_wake_word

FRAME = 1280                      # 80ms @16kHz
FOLLOW_UP_SEC = 1.0               # "자비스" 가 끝난 뒤 이 안에 말이 이어지면 그 말을 명령으로 받는다
FRAME_SEC = FRAME / config.SAMPLE_RATE


class SpeechSegmenter:
    """마이크 프레임을 받아 '말소리 한 덩어리'가 끝나면 그 오디오를 돌려준다."""
    END_SILENCE_SEC = 0.5          # 이만큼 조용하면 말이 끝난 것으로 본다
    MIN_LOUD_SEC = 0.24            # 이보다 짧은 소리(톡, 딸깍)는 무시
    PRE_ROLL = 4                   # 말 시작 직전 0.32초도 같이 담는다

    def __init__(self, max_sec=config.WAKEWORD_MAX_SEGMENT_SEC):
        self.max_frames = int(max_sec / FRAME_SEC)
        self.noise = None
        self.pre = deque(maxlen=self.PRE_ROLL)
        self.reset()

    def reset(self):
        self.buf, self.active, self.silent, self.loud = [], False, 0, 0
        self.pre.clear()

    def feed(self, chunk):
        rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
        if self.noise is None:
            self.noise = rms
        loud = rms > max(config.SPEECH_RMS_MIN, self.noise * 3)

        if not self.active:
            if not loud:
                self.noise = 0.95 * self.noise + 0.05 * rms      # 조용할 때 주변 소음 수준을 따라간다
                self.pre.append(chunk)
                return None
            self.active, self.buf, self.silent, self.loud = True, list(self.pre) + [chunk], 0, 1
            return None

        self.buf.append(chunk)
        if loud:
            self.silent, self.loud = 0, self.loud + 1
        else:
            self.silent += 1
        if self.silent * FRAME_SEC >= self.END_SILENCE_SEC or len(self.buf) >= self.max_frames:
            segment, loud_sec = np.concatenate(self.buf), self.loud * FRAME_SEC
            self.reset()
            if loud_sec >= self.MIN_LOUD_SEC:
                return segment
        return None


class WakeupWord:
    def __init__(self, stt=None, mode=config.WAKEWORD_MODE,
                 model_path=config.WAKEWORD_MODEL, threshold=config.WAKEWORD_THRESHOLD):
        """mode: 'hybrid'(Whisper + 모델) / 'stt'(Whisper 만) / 'model'(openwakeword 모델만)
        stt: stt.STT 인스턴스 (없으면 .env 키로 새로 만든다)"""
        self.mode = mode
        self.model_path, self.threshold = model_path, threshold
        self.model = self.model_name = self.stream = None
        self.use_model = mode in ("hybrid", "model")
        self.use_stt = mode in ("hybrid", "stt")
        if self.use_model:
            try:
                openwakeword.utils.download_models()   # 최초 1회 기본 모델 다운로드
            except Exception as e:
                print(f"[wakeword] 기본 모델 다운로드 건너뜀: {e}")
        if self.use_stt and stt is None:
            from voice.stt import STT
            stt = STT(config.load_openai_key())
        self.stt = stt
        self.segmenter = SpeechSegmenter()

    def open(self):
        if self.use_model:
            self.model = Model(wakeword_models=[self.model_path])
            self.model_name = next(iter(self.model.models))
        self.segmenter.reset()
        # 콜백으로 마이크 프레임을 큐에 계속 쌓는다 → Whisper 를 기다리는 동안 들어온 말도 놓치지 않음
        self.frames = queue.Queue()
        self.stream = sd.InputStream(samplerate=config.SAMPLE_RATE, channels=1, dtype="int16",
                                     blocksize=FRAME, device=resolve_input_device(),
                                     callback=lambda data, n, t, status: self.frames.put(data[:, 0].copy()))
        self.stream.start()

    def _next_frame(self):
        return self.frames.get(timeout=2.0)

    def _follow_up(self):
        """호출어 직후 이어서 말한 명령을 받아 적는다 (없으면 "")."""
        seg = SpeechSegmenter()
        seg.noise = self.segmenter.noise
        for _ in range(int(FOLLOW_UP_SEC / FRAME_SEC)):          # 말이 시작되기를 잠깐 기다림
            result = seg.feed(self._next_frame())
            if seg.active:
                break
        else:
            return ""
        while result is None and seg.active:                       # 말이 끝날 때까지
            result = seg.feed(self._next_frame())
        if result is None:
            return ""
        text = self.stt.transcribe_array(result, quiet=True).strip(" ,.!?~") if self.stt else ""
        if text:
            print(f"  [wakeword] 이어서 말한 명령: \"{text}\"")
        return text

    def check(self):
        """프레임 하나 처리. 깨어나면 '이어서 말한 명령'(없으면 "") 을, 아니면 None 을 돌려준다."""
        chunk = self._next_frame()

        if self.use_model:
            confidence = self.model.predict(chunk)[self.model_name]
            if confidence > self.threshold:
                print(f"Wakeword detected! (모델 {self.model_name}, confidence={confidence:.2f})")
                return self._follow_up() if self.use_stt else ""

        if self.use_stt:
            segment = self.segmenter.feed(chunk)
            if segment is not None:
                try:
                    text = self.stt.transcribe_array(segment, quiet=True)
                except Exception as e:
                    print(f"[wakeword] Whisper 실패: {e}")
                    return None
                span = find_wake_word(text)          # 발음 규칙으로 "자비스 / Jarvis" 찾기 (wake_match.py)
                if span:
                    rest = text[span[1]:].strip(" ,.!?~")
                    print(f"Wakeword detected! (들림: \"{text}\")")
                    return rest if len(rest) >= 2 else self._follow_up()
                if text:
                    print(f"  [wakeword] 들림: \"{text}\" — 호출어 아님")
                if self.model is not None:
                    self.model.reset()
        return None

    def wait(self):
        """호출어가 들릴 때까지 기다린 뒤 마이크를 닫는다(STT 가 마이크를 쓰도록).
        "자비스, 라면 하나 추가해줘" 처럼 이어 말했으면 뒷부분 문장을, 아니면 "" 를 돌려준다."""
        self.open()
        try:
            while True:
                result = self.check()
                if result is not None:
                    return result
        finally:
            self.close()

    def close(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
