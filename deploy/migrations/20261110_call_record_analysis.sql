-- Run once against an existing MySQL database after taking a backup.
-- Legacy tables are retained for rollback and data audit; no data is deleted.

CREATE TABLE IF NOT EXISTS call_record (
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
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_call_record_call_uuid (call_uuid)
);

CREATE TABLE IF NOT EXISTS call_transcript (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    call_uuid VARCHAR(128),
    language VARCHAR(32),
    messages_json LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_call_transcript_call_uuid (call_uuid)
);

CREATE TABLE IF NOT EXISTS call_analysis (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    call_uuid VARCHAR(128),
    analysis_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    schema_version VARCHAR(32),
    analyzer_version VARCHAR(64),
    model_name VARCHAR(128),
    result_json LONGTEXT,
    error_message TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_call_analysis_call_type_time
        (call_uuid, analysis_type, created_at)
);

INSERT INTO call_record (
    id, call_uuid, bot_mode, robot_code, robot_version_id,
    task_duration_sec, task_start_time, task_end_time, is_voicemail,
    technical_config, asr_cost, llm_cost, tts_cost, total_cost,
    asr_model_name, asr_param_config, asr_ttfb_list,
    asr_total_transcribed_chars, asr_total_audio_duration,
    asr_reconnect_count, asr_connection_requests,
    llm_model_name, llm_retry_count, llm_dialogue_turns, llm_ttfb_list,
    llm_total_generated_chars, llm_prompt_tokens, llm_completion_tokens,
    llm_cache_read_tokens, tts_model_name, tts_param_config, tts_ttfb_list,
    tts_total_output_duration, tts_total_chars,
    tts_reconnect_count, tts_connection_requests, created_at
)
SELECT
    p.id, p.call_uuid, p.bot_mode, p.robot_code, p.robot_version_id,
    p.task_duration_sec, p.task_start_time, p.task_end_time, p.is_voicemail,
    p.technical_config, p.asr_cost, p.llm_cost, p.tts_cost, p.total_cost,
    p.asr_model_name, p.asr_param_config, p.asr_ttfb_list,
    p.asr_total_transcribed_chars, p.asr_total_audio_duration,
    p.asr_reconnect_count, p.asr_connection_requests,
    p.llm_model_name, p.llm_retry_count, p.llm_dialogue_turns, p.llm_ttfb_list,
    p.llm_total_generated_chars, p.llm_prompt_tokens, p.llm_completion_tokens,
    p.llm_cache_read_tokens, p.tts_model_name, p.tts_param_config, p.tts_ttfb_list,
    p.tts_total_output_duration, p.tts_total_chars,
    p.tts_reconnect_count, p.tts_connection_requests,
    COALESCE(FROM_UNIXTIME(p.task_start_time / 1000), CURRENT_TIMESTAMP)
FROM process_record AS p
WHERE NOT EXISTS (SELECT 1 FROM call_record AS c WHERE c.id = p.id);

INSERT INTO call_transcript (
    id, call_uuid, language, messages_json, created_at
)
SELECT
    c.id, c.uuid, '', c.context,
    COALESCE(FROM_UNIXTIME(c.ctime1 / 1000), CURRENT_TIMESTAMP)
FROM context_log AS c
WHERE c.id IS NOT NULL
  AND c.uuid IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM call_transcript AS t WHERE t.id = c.id);
