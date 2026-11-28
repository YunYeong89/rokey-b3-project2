"""안내 음성 (TTS + 재생).

- edge-tts : Microsoft 신경망 음성 "선희" (config.TTS_VOICE). 키 불필요, 처음 만들 때만 인터넷 필요
- espeak-ng: edge-tts 를 못 쓸 때(설치 안 됨 / 인터넷 끊김) 대신 쓰는 로컬 기계음 — 프로그램이 멈추지 않게
만든 음성은 sounds/cache/ 에 저장해 두고 다음부터 바로 재생한다 (문구·목소리를 바꾸면 자동으로 새로 만듦).
"""
import asyncio
import ctypes
import hashlib
import os
import re
import subprocess
import tempfile

import numpy as np
import scipy.io.wavfile as wav
import sounddevice as sd

import config

CACHE_DIR = os.path.join(config.SOUNDS_DIR, "cache")


# ------------------------------------------------------------------ TTS 엔진 (text → wav 파일)
class EdgeTTS:
    name = "edge"
    settings = f"{config.TTS_VOICE}|{config.TTS_RATE}|{config.TTS_PITCH}"

    def __init__(self):
        import edge_tts   # 설치 안 되어 있으면 ImportError → espeak 로 대체
        self.edge_tts = edge_tts

    def synth(self, text, path):
        fd, mp3 = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            communicate = self.edge_tts.Communicate(text, config.TTS_VOICE, rate=config.TTS_RATE, pitch=config.TTS_PITCH)
            asyncio.run(communicate.save(mp3))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-ac", "1", "-ar", "24000", path],
                           check=True)
        finally:
            os.remove(mp3)


class EspeakTTS:
    name = "espeak"
    settings = config.ESPEAK_VOICE
    _lib = _rate = _cb = None
    _chunks = []
    _SINO = dict(zip("123456789", ["일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]))

    def __init__(self):
        if EspeakTTS._lib is None:
            lib = ctypes.CDLL("libespeak-ng.so.1")
            proto = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)

            def callback(samples, n, _events):
                if samples and n > 0:
                    EspeakTTS._chunks.append(ctypes.string_at(samples, n * 2))
                return 0

            EspeakTTS._cb = proto(callback)                          # GC 되지 않게 보관
            EspeakTTS._rate = lib.espeak_Initialize(2, 0, None, 0)   # 2 = SYNCHRONOUS
            lib.espeak_SetSynthCallback(EspeakTTS._cb)
            lib.espeak_SetVoiceByName(config.ESPEAK_VOICE.encode())
            lib.espeak_SetParameter(1, 150, 0)                       # 말하기 속도
            EspeakTTS._lib = lib

    @classmethod
    def _readable(cls, text):
        # espeak 은 "1번 장바구니" 를 "이번 참가꾼" 처럼 뭉개 읽는다(Whisper 로 확인).
        # 숫자를 한글로 바꾸고 쉼표로 끊어 주면 "일 번, 장바구니" 로 또렷해진다.
        return re.sub(r"([1-9])번\s*", lambda m: f"{cls._SINO[m.group(1)]} 번, ", text)

    def synth(self, text, path):
        EspeakTTS._chunks.clear()
        data = self._readable(text).encode("utf-8")
        self._lib.espeak_Synth(data, len(data) + 1, 0, 0, 0, 0x10, None, None)   # 0x10 = UTF-8
        self._lib.espeak_Synchronize()
        wav.write(path, self._rate, np.frombuffer(b"".join(EspeakTTS._chunks), dtype=np.int16))


# ------------------------------------------------------------------ Speaker
class Speaker:
    ENGINES = (EdgeTTS, EspeakTTS)    # 앞에서부터 시도

    def __init__(self):
        self._engines = {}
        os.makedirs(CACHE_DIR, exist_ok=True)

    def _engine(self, cls):
        if cls not in self._engines:
            try:
                self._engines[cls] = cls()
            except Exception:
                self._engines[cls] = None
        return self._engines[cls]

    def _synth_cached(self, text):
        for cls in self.ENGINES:
            digest = hashlib.sha1(f"{cls.name}|{cls.settings}|{text}".encode()).hexdigest()[:12]
            path = os.path.join(CACHE_DIR, f"{cls.name}_{digest}.wav")
            if os.path.exists(path):
                return path
            engine = self._engine(cls)
            if engine is None:
                continue
            try:
                engine.synth(text, path)
                return path
            except Exception as e:
                print(f"  [speaker] {cls.name} 음성 합성 실패 → 다음 방법 ({type(e).__name__}: {str(e)[:80]})")
                self._engines[cls] = None
                if os.path.exists(path):
                    os.remove(path)
        raise RuntimeError("안내 음성을 만들 수 없습니다 (edge-tts / espeak-ng 모두 실패)")

    def prepare(self, key, **fmt):
        """(문구, wav 경로). 처음이면 합성해서 캐시에 저장한다."""
        text = config.VOICE_MESSAGES[key].format(**fmt)
        return text, self._synth_cached(text)

    def all_messages(self):
        """(key, fmt) 목록 — 장바구니 번호가 들어가는 멘트는 번호별로"""
        items = []
        for key, text in config.VOICE_MESSAGES.items():
            if "{basket}" in text:
                items += [(key, {"basket": b}) for b in config.PICKUP_BASKETS]
            else:
                items.append((key, {}))
        return items

    @staticmethod
    def _beep():
        rate = 24000
        t = np.arange(int(rate * 0.18)) / rate
        tone = (0.4 * np.sin(2 * np.pi * 1200 * t) * 32767).astype(np.int16)
        gap = np.zeros(int(rate * 0.08), dtype=np.int16)
        sd.play(np.concatenate([tone, gap, tone, gap]), rate)
        sd.wait()

    def say(self, key, **fmt):
        """안내 멘트 재생. key 는 config.VOICE_MESSAGES 의 이름, fmt 는 {basket} 등"""
        text, path = self.prepare(key, **fmt)
        warning = key in config.WARNING_MESSAGES
        print(f"  🔊 {'⚠️ ' if warning else ''}{text}")
        if warning:
            self._beep()
        rate, data = wav.read(path)
        sd.play(data, rate)
        sd.wait()
        return text
