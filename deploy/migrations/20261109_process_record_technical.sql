-- Run once against the existing MySQL database after taking a backup.
-- The legacy table is retained for rollback and audit.

RENAME TABLE process_record TO process_record_legacy;

CREATE TABLE process_record (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    call_uuid VARCHAR(128),
    bot_mode VARCHAR(32),
    robot_code VARCHAR(128),
    robot_version_id VARCHAR(128) NULL,
    task_duration_sec DOUBLE,
    task_start_time BIGINT,
    task_end_time BIGINT,
    is_voicemail VARCHAR(16),
    technical_config TEXT,
    asr_cost DOUBLE,
    llm_cost DOUBLE,
    tts_cost DOUBLE,
    total_cost DOUBLE,
    asr_model_name TEXT,
    asr_param_config TEXT,
    asr_ttfb_list TEXT,
    asr_total_transcribed_chars DOUBLE,
    asr_total_audio_duration DOUBLE,
    asr_reconnect_count INT,
    asr_connection_requests INT,
    llm_model_name TEXT,
    llm_retry_count INT,
    llm_dialogue_turns INT,
    llm_ttfb_list TEXT,
    llm_total_generated_chars DOUBLE,
    llm_prompt_tokens INT,
    llm_completion_tokens INT,
    llm_cache_read_tokens INT,
    tts_model_name TEXT,
    tts_param_config TEXT,
    tts_ttfb_list TEXT,
    tts_total_output_duration DOUBLE,
    tts_total_chars DOUBLE,
    tts_reconnect_count INT,
    tts_connection_requests INT,
    INDEX idx_process_record_call_uuid (call_uuid)
);

INSERT INTO process_record (
    call_uuid, task_duration_sec, task_start_time, task_end_time,
    is_voicemail, asr_cost, llm_cost, tts_cost, total_cost,
    asr_model_name, asr_param_config, asr_ttfb_list,
    asr_total_transcribed_chars, asr_total_audio_duration,
    asr_reconnect_count, asr_connection_requests, llm_model_name,
    llm_retry_count, llm_dialogue_turns, llm_ttfb_list,
    llm_total_generated_chars, llm_prompt_tokens, llm_completion_tokens,
    llm_cache_read_tokens, tts_model_name, tts_param_config,
    tts_ttfb_list, tts_total_output_duration, tts_total_chars,
    tts_reconnect_count, tts_connection_requests, robot_code
)
SELECT
    call_uuid, task_duration_sec, task_start_time, task_end_time,
    is_voicemail, asr_cost, llm_cost, tts_cost, total_cost,
    asr_model_name, asr_param_config, asr_ttfb_list,
    asr_total_transcribed_chars, asr_total_audio_duration,
    asr_reconnect_count, asr_connection_requests, llm_model_name,
    llm_retry_count, llm_dialogue_turns, llm_ttfb_list,
    llm_total_generated_chars, llm_prompt_tokens, llm_completion_tokens,
    llm_cache_read_tokens, tts_model_name, tts_param_config,
    tts_ttfb_list, tts_total_output_duration, tts_total_chars,
    tts_reconnect_count, tts_connection_requests, robot_code
FROM process_record_legacy;
