VOICE_ID = "71a7ad14-091c-4e8e-a314-022ece01c121"


def valid_config():
    return {
        "schema_version": "robot-app/v1",
        "app": {"code": "example-standard-en", "name": "Standard English"},
        "prompt": {"system_template": "Speak to {{user_name}}."},
        "variables": [
            {
                "key": "user_name", "type": "string", "required": True,
                "sensitive": True, "max_length": 100,
            },
            {"key": "contact_time", "type": "datetime", "required": False, "default": ""},
        ],
        "asr": {
            "provider": "deepgram", "model": "nova-2", "language": "en-US",
            "sample_rate": 8000, "encoding": "pcm_s16le", "interim_results": True,
            "endpointing_ms": 100,
        },
        "llm": {
            "provider": "deepseek", "model": "deepseek-flash", "temperature": 0.7,
            "top_p": 0.9, "context_rounds": 10, "max_tokens": 500, "timeout_ms": 6000,
        },
        "tts": {
            "provider": "cartesia", "model": "sonic-2", "voice_id": VOICE_ID,
            "language": "en-US", "speed": 0.9, "volume": 80,
            "sample_rate": 8000, "encoding": "pcm_s16le",
        },
        "flow": {
            "welcomeNode": {"content": "Am I speaking with {{user_name}}?"},
            "endNode": {
                "content": "Goodbye",
                "hangUpConfig": {
                    "delayHangUp": {"enabled": True, "delaySecond": 1},
                    "otherHangUp": {
                        "minInteractions": 15, "minCallDurationSec": 300,
                        "content": "Goodbye",
                    },
                },
            },
            "noResponse": {
                "timeoutSec": 6, "noResponseReply": "Hello?", "maxNoResponseCount": 3,
                "noResponseHangUp": {"enabled": True, "hangupMessage": "Goodbye"},
            },
            "voicemailNode": {
                "enabled": True, "action": "playThenHangup", "content": "Goodbye",
            },
        },
        "call_policy": {"allow_interruptions": True, "max_call_duration_sec": 180},
    }
