"""Hearing what you said, on this machine.

sounddevice and faster-whisper are both optional and both imported lazily.
Without them this module says so and the daemon runs exactly as it did before.
That is the same bargain the Claude picker takes: the extra may be missing,
the music may not stop.

Nothing here knows about the DJ. It records, it transcribes, and voice.py
decides what any of it means.
"""

import logging
import threading

log = logging.getLogger("music-dj")

SAMPLE_RATE = 16000       # what Whisper wants; anything else it resamples
MAX_SECONDS = 15          # a held key that outlives this is a forgotten key
DEFAULT_MODEL = "small"   # base mishears French; medium is four times slower
DEFAULT_LANGUAGE = "fr"
# Whisper answers near-silence with a confident-looking phrase (its training
# data was full of subtitle boilerplate), so the text alone cannot tell you
# whether anyone spoke. This is the number that can.
DEFAULT_NO_SPEECH = 0.6


# Why the last import attempt failed, keyed by package. Kept because the two
# ways to fail want opposite advice, and the module used to give the same
# advice to both.
_IMPORT_ERRORS = {}


def _import_audio():
    """sounddevice and numpy, or None. Separated so tests can remove them."""
    try:
        import numpy
        import sounddevice
        _IMPORT_ERRORS.pop("sounddevice", None)
        return sounddevice, numpy
    except Exception as exc:
        _IMPORT_ERRORS["sounddevice"] = exc
        return None


def _import_whisper():
    try:
        from faster_whisper import WhisperModel
        _IMPORT_ERRORS.pop("faster-whisper", None)
        return WhisperModel
    except Exception as exc:
        _IMPORT_ERRORS["faster-whisper"] = exc
        return None


def available():
    return bool(_import_audio()) and bool(_import_whisper())


def unavailable_reason():
    """Why voice is off, in one line, or None when it is on.

    available() answers yes or no; this answers why, because a package that
    is missing and a package that is installed and will not load want
    opposite advice. Being told to install what you have already installed
    sends you round the same loop -- which is what happens on an ARM64
    Windows machine, where sounddevice asks PortAudio for a DLL its own x64
    wheel never ships.
    """
    reasons = []
    for name, load in (("sounddevice", _import_audio),
                       ("faster-whisper", _import_whisper)):
        if load() is not None:
            continue
        exc = _IMPORT_ERRORS.get(name)
        if exc is None or isinstance(exc, ImportError):
            reasons.append("%s is not installed" % name)
        else:
            reasons.append("%s is installed but will not load (%s: %s)"
                           % (name, type(exc).__name__, exc))
    return "; ".join(reasons) or None


def usable(text, no_speech_prob, threshold=DEFAULT_NO_SPEECH):
    """Whether to act on what Whisper heard.

    Two ways to get nothing: an empty string, and a phrase invented over a
    cough. The second one is the dangerous one, because it looks like speech.
    """
    if not (text or "").strip():
        return False
    if no_speech_prob is not None and no_speech_prob > threshold:
        return False
    return True


class Recorder:
    """The microphone, open only while start() and stop() bracket it."""

    def __init__(self, sample_rate=SAMPLE_RATE, max_seconds=MAX_SECONDS):
        self.sample_rate = sample_rate
        self.max_frames = sample_rate * max_seconds
        self._stream = None
        self._blocks = []
        self._frames = 0
        self._lock = threading.Lock()

    def start(self):
        audio = _import_audio()
        if not audio or self._stream is not None:
            return
        sounddevice, _ = audio
        self._blocks, self._frames = [], 0

        def feed(indata, frames, time_info, status):
            # Called on PortAudio's own thread. Copy, because the buffer it
            # hands over is reused the moment this returns.
            with self._lock:
                if self._frames >= self.max_frames:
                    return
                self._blocks.append(indata.copy())
                self._frames += frames

        try:
            self._stream = sounddevice.InputStream(
                samplerate=self.sample_rate, channels=1, dtype="float32",
                callback=feed)
            self._stream.start()
        except Exception:
            log.info("could not open the microphone", exc_info=True)
            self._stream = None

    def stop(self):
        """Close the mic and hand back the clip, or None if there is none."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("closing the microphone failed", exc_info=True)
        audio = _import_audio()
        with self._lock:
            blocks, self._blocks, self._frames = self._blocks, [], 0
        if not audio or not blocks:
            return None
        _, numpy = audio
        return numpy.concatenate(blocks).reshape(-1)[:self.max_frames]


class Transcriber:
    """Whisper, loaded once and kept."""

    def __init__(self, model=DEFAULT_MODEL, language=DEFAULT_LANGUAGE,
                 no_speech=DEFAULT_NO_SPEECH):
        self.model_name = model
        self.language = language
        self.no_speech = no_speech
        self._model = None
        self._lock = threading.Lock()

    def warm(self):
        """Load the model. Slow, and worth doing before the first press."""
        with self._lock:
            if self._model is not None:
                return self._model
            WhisperModel = _import_whisper()
            if WhisperModel is None:
                return None
            try:
                # int8 on the CPU: a phrase comes back in about a second,
                # against three or four in float32, for no audible loss on
                # something this short.
                self._model = WhisperModel(self.model_name, device="cpu",
                                           compute_type="int8")
            except Exception:
                log.info("could not load the %s model; voice is off",
                         self.model_name, exc_info=True)
            return self._model

    def warm_in_background(self):
        threading.Thread(target=self.warm, daemon=True,
                         name="whisper-warm").start()

    def __call__(self, audio):
        """The clip as a French sentence, or "" when nobody said anything."""
        if audio is None:
            return ""
        model = self.warm()
        if model is None:
            return ""
        try:
            segments, _ = model.transcribe(audio, language=self.language,
                                           vad_filter=True)
            segments = list(segments)
        except Exception:
            log.info("transcription failed", exc_info=True)
            return ""
        text = " ".join((s.text or "").strip() for s in segments).strip()
        # One probability for the clip: these are a few seconds long and split
        # into one or two segments, so the first segment speaks for all of it.
        prob = getattr(segments[0], "no_speech_prob", None) if segments else None
        return text if usable(text, prob, self.no_speech) else ""


def main():
    """python -m daemon.listen --warm: pull the model down, once."""
    import argparse
    parser = argparse.ArgumentParser(prog="music-dj listen")
    parser.add_argument("--warm", action="store_true",
                        help="download and load the model, then exit")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    reason = unavailable_reason()
    if reason:
        print("Voice is off: %s" % reason)
        if "not installed" in reason:
            print("    python -m pip install -r app/requirements-voice.txt")
        raise SystemExit(1)
    print("Loading %s. The first run downloads about 500MB." % args.model)
    if Transcriber(model=args.model).warm() is None:
        raise SystemExit(1)
    print("Ready. Hold Alt+J and talk to the DJ.")


if __name__ == "__main__":
    main()
