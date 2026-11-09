import logging

from collection_agents.services.audio_assets import (
    DEFAULT_CARTESIA_VOICE_ID,
    DEFAULT_LOCALE,
    DEFAULT_MODEL,
    SAMPLE_RATE as AUDIO_ASSET_SAMPLE_RATE,
    TEXT_TYPE_VOICEMAIL,
    find_audio_asset,
    load_audio_frame,
    normalize_text as normalize_audio_asset_text,
    split_voicemail_segments,
)


class VoicemailAudioRepository:
    def __init__(
        self,
        *,
        robot_code,
        tts_config,
        text,
        cache_enabled,
        on_playback,
    ):
        self._robot_code = robot_code
        self._tts_config = tts_config
        self._text = text or ""
        self._cache_enabled = cache_enabled
        self._on_playback = on_playback

    def _remote_config(self):
        live_text, cached_text = split_voicemail_segments(self._text)
        return {
            "bot_code": self._robot_code,
            "provider": (self._tts_config.get("provider") or "cartesia").strip().lower(),
            "voice_id": self._tts_config.get("voice_id") or DEFAULT_CARTESIA_VOICE_ID,
            "model": self._tts_config.get("model") or DEFAULT_MODEL,
            "locale": self._tts_config.get("locale") or DEFAULT_LOCALE,
            "sample_rate": AUDIO_ASSET_SAMPLE_RATE,
            "audio_format": "wav",
            "text_type": TEXT_TYPE_VOICEMAIL,
            "live_text": live_text,
            "text": cached_text,
            "full_text": self._text,
        }

    @staticmethod
    def _local_config(asset):
        if not asset:
            return None
        return {
            key: asset.get(key)
            for key in (
                "bot_code", "provider", "voice_id", "model", "locale",
                "sample_rate", "audio_format", "text_type", "text_hash",
                "text", "file_path", "status", "enabled", "fallback",
            )
        }

    def _mark_playback(self, source, remote, **kwargs):
        playback = {"source": source, "remote": remote, **kwargs}
        self._on_playback(playback)
        return playback

    def find_cached_audio(self):
        remote = self._remote_config()
        if not self._cache_enabled:
            self._mark_playback("live_tts", remote, reason="cache_segment_missing")
            return None

        asset = find_audio_asset(self._robot_code)
        if not asset:
            self._mark_playback("live_tts", remote, reason="cache_miss")
            logging.info(
                "audio_asset_miss bot_code=%s reason=cache_miss",
                self._robot_code,
            )
            return None

        expected = {
            "provider": remote["provider"],
            "voice_id": remote["voice_id"],
            "model": remote["model"],
            "locale": remote["locale"],
            "sample_rate": remote["sample_rate"],
            "audio_format": remote["audio_format"],
            "text_type": remote["text_type"],
            "text": normalize_audio_asset_text(remote["text"]),
        }
        mismatches = []
        for field, expected_value in expected.items():
            actual_value = (
                normalize_audio_asset_text(asset.get(field, ""))
                if field == "text"
                else asset.get(field)
            )
            if actual_value != expected_value:
                mismatches.append(
                    f"{field}: expected={expected_value!r} actual={actual_value!r}"
                )

        local = self._local_config(asset)
        if mismatches:
            self._mark_playback(
                "live_tts",
                remote,
                reason="cache_mismatch",
                mismatches=mismatches,
                local=local,
            )
            logging.info(
                "audio_asset_miss bot_code=%s reason=cache_mismatch mismatches=%s",
                self._robot_code,
                mismatches,
            )
            return None

        try:
            frame = load_audio_frame(asset)
        except Exception as exc:
            self._mark_playback(
                "live_tts",
                remote,
                reason="cache_load_failed",
                local=local,
            )
            logging.warning(
                "audio_asset_miss bot_code=%s reason=cache_load_failed error=%s",
                self._robot_code,
                exc,
            )
            return None

        self._mark_playback("local_cache", remote, local=local)
        logging.info(
            "audio_asset_hit bot_code=%s text_hash=%s file_path=%s",
            self._robot_code,
            asset.get("text_hash"),
            asset.get("file_path"),
        )
        return {"asset": asset, "frame": frame}
