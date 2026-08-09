"""Key down, music down, microphone open. Key up, put it all back.

This is the only module that knows about both the microphone and the DJ.
listen.py hears, hotkey.py watches the key, and neither of them knows what
any of it is for.
"""

import asyncio
import logging

from . import hotkey, listen

log = logging.getLogger("music-dj")

DUCK_LEVEL = 0.15         # quiet enough to talk over, loud enough to still be on
# Generous for a clip capped at 15 seconds. It exists for the transcriber that
# never comes back at all, not for the slow one.
TRANSCRIBE_TIMEOUT = 60


class Voice:
    def __init__(self, dj, loop, *, recorder, transcriber,
                 duck_level=DUCK_LEVEL, timeout=TRANSCRIBE_TIMEOUT):
        self.dj, self.loop = dj, loop
        self.recorder, self.transcriber = recorder, transcriber
        self.duck_level = duck_level
        self.timeout = timeout
        # Two flags, because they answer two different questions. `recording`
        # is true only between the press and the release; `busy` spans the
        # whole cycle, transcription included. One flag doing both meant a
        # second release finished a recording that was never started, which
        # cleared the flag the real release needed and left the microphone
        # open for the rest of the session.
        self.recording = False
        self.busy = False
        # The loop keeps only weak references to tasks, so a bare create_task
        # can be collected mid-flight. Same pattern as core.DJ._spawn.
        self._tasks = set()

    # ------------------------------------------- called from the hotkey thread

    def pressed(self):
        self.loop.call_soon_threadsafe(self._start)

    def released(self):
        self.loop.call_soon_threadsafe(self._stop)

    # ------------------------------------------------------- on the event loop

    def _start(self):
        if self.busy:
            # Key auto-repeat that got past MOD_NOREPEAT, or a second press
            # while the last sentence is still being transcribed.
            return
        self.busy = self.recording = True
        try:
            self.recorder.start()
            self.dj.set_listening(True)
            # Spawned last, so a microphone that refuses to open has nothing
            # ducked to put back.
            self._spawn(self.dj.duck(self.duck_level))
        except Exception:
            # Latching the flags on the way out would swallow every later
            # press, which is the same stuck microphone by another door.
            self.busy = self.recording = False
            self.dj.set_listening(False)
            log.info("could not start listening", exc_info=True)

    def _stop(self):
        if not self.recording:
            # A release can only end a recording that exists. Guarding on
            # `busy` instead let a second release start a phantom _finish for
            # a recording nobody made.
            return
        self.recording = False
        # The pulse stops when the key comes up, not a second or two later
        # when transcription finishes. Clearing it in _finish meant the
        # overlay went on saying the microphone was open after you let go.
        self.dj.set_listening(False)
        self._spawn(self._finish())

    async def _finish(self):
        text = ""
        try:
            audio = self.recorder.stop()
            if audio is not None:
                # Whisper holds the GIL for a second or more. On the loop it
                # would freeze playback events for the whole transcription.
                # The timeout abandons the wait, not the thread -- a worker
                # cannot be cancelled -- but the music comes back either way.
                text = await asyncio.wait_for(
                    self.loop.run_in_executor(None, self.transcriber, audio),
                    timeout=self.timeout)
        except Exception:
            log.info("could not make out what was said", exc_info=True)
        finally:
            # Whatever happened above, the music comes back. Leaving it at
            # 15% reads as a mixing problem for the rest of the session and
            # nobody would connect it to having spoken.
            await self.dj.unduck()
            self.busy = False
        if text:
            await self.dj.on_steer(text)
        else:
            # A press that changes nothing and says nothing is indistinguishable
            # from a key that never worked, which is exactly how it was read.
            self.dj.heard_nothing()

    def _spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task):
        self._tasks.discard(task)
        # on_steer runs through here, and it holds the Claude call, the queue
        # rebuild and play_next. Under pythonw even asyncio's own warning
        # about an unretrieved exception goes nowhere. Same as core.DJ.
        if not task.cancelled() and task.exception() is not None:
            log.error("voice task failed", exc_info=task.exception())


def start(dj, loop, config):
    """Wire the key to the DJ. Returns something with .stop(), or None.

    Every way this can decline is silent for the rest of the app: no key, no
    packages, no microphone, and the daemon runs as it always did.
    """
    cfg = (config or {}).get("voice") or {}
    if cfg.get("enabled") is False:
        return None
    if not listen.available():
        # Say which package and why. This line used to recommend pip install
        # whatever the cause, so a package that was installed and would not
        # load sent you back to install it again.
        reason = listen.unavailable_reason() or "the voice packages are unavailable"
        log.info("voice off: %s", reason)
        if "not installed" in reason:
            log.info("to talk to the DJ: pip install -r "
                     "app/requirements-voice.txt, then "
                     "python -m daemon.listen --warm")
        return None

    transcriber = listen.Transcriber(
        model=cfg.get("model") or listen.DEFAULT_MODEL,
        language=cfg.get("language") or listen.DEFAULT_LANGUAGE,
        no_speech=cfg.get("no_speech_threshold", listen.DEFAULT_NO_SPEECH))
    voice = Voice(dj, loop, recorder=listen.Recorder(),
                  transcriber=transcriber,
                  duck_level=cfg.get("duck_level", DUCK_LEVEL))

    key = hotkey.start(cfg.get("hotkey") or "alt+j",
                       voice.pressed, voice.released)
    if key is None:
        return None
    # Loading the model takes a few seconds. Do it now rather than inside the
    # first press, where it reads as the key not working.
    transcriber.warm_in_background()
    return key
