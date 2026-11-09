"""Persistence for the Vosk-vs-Deepgram shadow comparison (ASR A/B table).

Written once per call, at end of call, for robots running local Vosk with the
Deepgram shadow enabled. Stores the whole-call transcripts from both engines so
accuracy can be compared offline. Fully best-effort: any DB error is logged and
swallowed so it can never affect the call.

Table `asr_shadow_compare` is provisioned by ops (see sql/asr_shadow_compare.sql).
If the table does not exist yet, inserts fail and are logged only.
"""
import json
import logging
import time

from sqlalchemy import text

from my_utils.mysql import MysqlClient

_INSERT_SQL = text(
    """
    INSERT INTO asr_shadow_compare
        (call_uuid, robot_code, language, vosk_model, deepgram_model,
         vosk_text, deepgram_text, vosk_segments, deepgram_segments, ctime1)
    VALUES
        (:call_uuid, :robot_code, :language, :vosk_model, :deepgram_model,
         :vosk_text, :deepgram_text, :vosk_segments, :deepgram_segments, :ctime1)
    """
)


def save_asr_shadow_compare(
    *,
    call_uuid,
    robot_code,
    language,
    vosk_model,
    deepgram_model,
    vosk_text,
    deepgram_text,
    vosk_segments=None,
    deepgram_segments=None,
):
    """Insert one comparison row for the call. Never raises."""
    try:
        params = {
            "call_uuid": call_uuid or "",
            "robot_code": robot_code or "",
            "language": language or "",
            "vosk_model": vosk_model or "",
            "deepgram_model": deepgram_model or "",
            "vosk_text": (vosk_text or "")[:8000],
            "deepgram_text": (deepgram_text or "")[:8000],
            "vosk_segments": json.dumps(vosk_segments or [], ensure_ascii=False),
            "deepgram_segments": json.dumps(deepgram_segments or [], ensure_ascii=False),
            "ctime1": int(time.time() * 1000),
        }
        MysqlClient.execute(_INSERT_SQL, params)
        logging.info(
            "asr_shadow_compare inserted for call_uuid=%s (vosk=%d chars, deepgram=%d chars)",
            params["call_uuid"],
            len(params["vosk_text"]),
            len(params["deepgram_text"]),
        )
    except Exception as exc:
        logging.warning(f"save_asr_shadow_compare failed (ignored): {exc}")
