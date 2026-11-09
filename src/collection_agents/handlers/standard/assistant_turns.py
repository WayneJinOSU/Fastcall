import logging

_logger = logging.getLogger(__name__)

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    LLMAssistantPushAggregationFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from pipecat.utils.string import TextPartForConcatenation, concatenate_aggregated_text

class ContextSafeAssistantTurnGate(FrameProcessor):
    """Only commits uninterrupted assistant turns to downstream context."""

    def __init__(self, interruption_state: dict | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._in_assistant_turn = False
        self._buffered_text: list[TextPartForConcatenation] = []
        self._interruption_state = interruption_state if isinstance(interruption_state, dict) else {}
        self._interruption_state.setdefault("assistant_turn_interrupted", False)
        self._pending_commit_text: str | None = None  # buffered until BotStoppedSpeakingFrame

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            if self._pending_commit_text:
                # New LLM turn started while previous TTS was still pending commit.
                # This only happens when the old TTS was cut short by an interruption
                # (otherwise BotStoppedSpeakingFrame would have already cleared it).
                logging.info(
                    "[ContextSafeAssistantTurnGate] New LLM turn started with stale pending commit — dropping: %r",
                    self._pending_commit_text[:200],
                )
                self._pending_commit_text = None
            self._in_assistant_turn = True
            self._interruption_state["assistant_turn_interrupted"] = False
            self._buffered_text = []
            logging.info(
                "[ContextSafeAssistantTurnGate] Assistant turn started direction=%s",
                direction,
            )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            # Per-chunk TextFrame logs are too noisy for production INFO logs.
            # Uncomment when debugging assistant context buffering.
            # logging.debug(
            #     "[ContextSafeAssistantTurnGate] Saw TextFrame direction=%s in_turn=%s append_to_context=%s text=%r",
            #     direction,
            #     self._in_assistant_turn,
            #     getattr(frame, "append_to_context", None),
            #     frame.text[:120],
            # )
            if self._in_assistant_turn and frame.append_to_context:
                self._buffered_text.append(
                    TextPartForConcatenation(
                        frame.text,
                        includes_inter_part_spaces=frame.includes_inter_frame_spaces,
                    )
                )
                frame.append_to_context = False
                # Per-chunk TextFrame logs are too noisy for production INFO logs.
                # Uncomment when debugging assistant context buffering.
                # logging.debug(
                #     "[ContextSafeAssistantTurnGate] Buffered assistant text chunk and suppressed immediate context append: %r",
                #     frame.text[:120],
                # )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            interrupted = bool(self._interruption_state.get("assistant_turn_interrupted"))
            logging.info(
                "[ContextSafeAssistantTurnGate] Assistant turn ending direction=%s interrupted=%s buffered_chunks=%d",
                direction,
                interrupted,
                len(self._buffered_text),
            )
            if self._in_assistant_turn and not interrupted and self._buffered_text:
                aggregated = concatenate_aggregated_text(self._buffered_text)
                if aggregated:
                    # Don't commit yet — TTS may still be playing. Hold until BotStoppedSpeakingFrame
                    # to detect whether the user interrupted mid-speech.
                    self._pending_commit_text = aggregated
                    logging.info(
                        "[ContextSafeAssistantTurnGate] Holding assistant turn pending TTS completion: %r",
                        aggregated[:200],
                    )
            elif self._in_assistant_turn and interrupted:
                dropped = concatenate_aggregated_text(self._buffered_text).strip()
                logging.info(
                    "[ContextSafeAssistantTurnGate] Dropping interrupted assistant turn from context: %r",
                    dropped[:200],
                )
            self._in_assistant_turn = False
            self._interruption_state["assistant_turn_interrupted"] = False
            self._buffered_text = []
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, BotStoppedSpeakingFrame):
            if self._pending_commit_text:
                interrupted = bool(self._interruption_state.get("assistant_turn_interrupted"))
                if not interrupted:
                    logging.info(
                        "[ContextSafeAssistantTurnGate] TTS completed normally, committing assistant turn: %r",
                        self._pending_commit_text[:200],
                    )
                    text_frame = TextFrame(self._pending_commit_text)
                    text_frame.includes_inter_frame_spaces = True
                    await self.push_frame(text_frame, FrameDirection.DOWNSTREAM)
                    await self.push_frame(LLMAssistantPushAggregationFrame(), FrameDirection.DOWNSTREAM)
                else:
                    logging.info(
                        "[ContextSafeAssistantTurnGate] TTS was interrupted, dropping assistant turn: %r",
                        self._pending_commit_text[:200],
                    )
                self._pending_commit_text = None
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (EndFrame, CancelFrame)):
            if self._pending_commit_text:
                logging.info(
                    "[ContextSafeAssistantTurnGate] Dropping pending commit on %s",
                    type(frame).__name__,
                )
                self._pending_commit_text = None
            if self._in_assistant_turn and self._buffered_text:
                logging.info(
                    "[ContextSafeAssistantTurnGate] Resetting assistant turn state on %s; interrupted=%s buffered_chunks=%d",
                    type(frame).__name__,
                    bool(self._interruption_state.get("assistant_turn_interrupted")),
                    len(self._buffered_text),
                )
            self._in_assistant_turn = False
            self._interruption_state["assistant_turn_interrupted"] = False
            self._buffered_text = []
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
