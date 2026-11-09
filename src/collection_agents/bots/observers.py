import asyncio
import logging

from pipecat.frames.frames import StartFrame, TTSAudioRawFrame
from pipecat.observers.base_observer import BaseObserver


class PipelineFlowWatchdog(BaseObserver):
    """Logs an ERROR if welcome audio does not reach the transport output."""

    def __init__(self, sink, timeout_sec: float = 10.0):
        super().__init__()
        self._sink = sink
        self._timeout_sec = timeout_sec
        self._armed = False
        self._fired = False
        self._watch_task: asyncio.Task | None = None

    def rearm(self):
        """Re-arm the watchdog when a new expected-flow point is reached (on_answer)."""
        if self._fired:
            return
        self._armed = True
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
        self._watch_task = asyncio.ensure_future(self._watch_loop())

    async def on_push_frame(self, data):
        if self._fired:
            return
        if not self._armed and isinstance(data.frame, StartFrame):
            self._armed = True
            self._watch_task = asyncio.ensure_future(self._watch_loop())
            return
        if (
            self._armed
            and data.destination is self._sink
            and isinstance(data.frame, TTSAudioRawFrame)
        ):
            self._disarm()

    async def _watch_loop(self):
        try:
            await asyncio.sleep(self._timeout_sec)
            if self._armed and not self._fired:
                self._fired = True
                logging.error(
                    "[pipeline_watchdog] no bot audio frame reached the pipeline sink %ss after "
                    "pipeline start/answer; pipeline may be stalled or the pod dead",
                    self._timeout_sec,
                )
        except asyncio.CancelledError:
            raise

    def _disarm(self):
        self._armed = False
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
        self._watch_task = None

    async def cleanup(self):
        self._disarm()
