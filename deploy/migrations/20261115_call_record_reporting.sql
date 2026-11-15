-- Run once after 20261110_call_record_analysis.sql against the Agent database.
-- Existing rows remain unclassified; call_uuid is intentionally not unique.

ALTER TABLE call_record
    ADD COLUMN call_source VARCHAR(32) NULL AFTER call_uuid,
    ADD COLUMN claim_generation INT NULL AFTER call_source,
    ADD INDEX idx_call_record_start_id (task_start_time, id),
    ADD INDEX idx_call_record_source_start (call_source, task_start_time, id);
