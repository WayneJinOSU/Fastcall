from dataclasses import dataclass, field

from pipecat.frames.frames import Frame


@dataclass(frozen=True)
class VoicemailDetectedEvent:
    """Evidence emitted when a processor identifies a voicemail prompt."""

    transcript_text: str = ""
    trigger_text: str = ""
    matched_text: str = ""
    source: str = "standard"
    item_id: str = ""


@dataclass
class VoicemailDetectedFrame(Frame):
    event: VoicemailDetectedEvent = field(default_factory=VoicemailDetectedEvent)
