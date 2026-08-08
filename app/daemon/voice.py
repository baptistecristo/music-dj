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


class Voice:
    def __init__(self, dj, loop, *, recorder, transcriber,
                 duck_level=DUCK_LEVEL):
        self.dj, self.loop = dj, loop
        self.recorder, self.transcriber = recorder, transcriber
        self.duck_level = duck_level
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
        self.busy = True
        self.recorder.start()
        self.dj.set_listening(True)
        self._spawn(self.dj.duck(self.duck_level))

    def _stop(self):
        if not self.busy:
            return
        self._spawn(self._finish())

    async def _finish(self):
        text = ""
        try:
            audio = self.recorder.stop()
            if audio is not None:
                # Whisper holds the GIL for a second or more. On the loop it
                # would freeze playback events for the whole transcription.
                text = await self.loop.run_in_executor(
                    None, self.transcriber, audio)
        except Exception:
            log.info("could not make out what was said", exc_info=True)
        finally:
            # Whatever happened above, the music comes back. Leaving it at
            # 15% reads as a mixing problem for the rest of the session and
            # nobody would connect it to having spoken.
            self.dj.set_listening(False)
            await self.dj.unduck()
            self.busy = False
        if text:
            await self.dj.on_steer(text)

    def _spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task


def start(dj, loop, config):
    """Wire the key to the DJ. Returns something with .stop(), or None.

    Every way this can decline is silent for the rest of the app: no key, no
    packages, no microphone, and the daemon runs as it always did.
    """
    cfg = (config or {}).get("voice") or {}
    if cfg.get("enabled") is False:
        return None
    if not listen.available():
        log.info("voice off. To talk to the DJ: pip install -r "
                 "app/requirements-voice.txt, then python -m daemon.listen --warm")
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
