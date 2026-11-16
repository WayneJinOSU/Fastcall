-- Fast-Call current schema for a new MySQL database.
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `robot_app` (
  `id` varchar(40) NOT NULL,
  `app_code` varchar(128) NOT NULL,
  `name` varchar(255) NOT NULL,
  `enabled` tinyint(1) NOT NULL,
  `draft_json` json NOT NULL,
  `draft_revision` bigint NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `app_code` (`app_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `robot_app_version` (
  `id` varchar(40) NOT NULL,
  `app_id` varchar(40) NOT NULL,
  `version_no` bigint NOT NULL,
  `source_draft_revision` bigint NOT NULL,
  `content_json` json NOT NULL,
  `config_hash` varchar(71) NOT NULL,
  `schema_version` varchar(32) NOT NULL,
  `runtime_engine` varchar(32) NOT NULL,
  `runtime_adapter` varchar(64) NOT NULL,
  `runtime_catalog_version` varchar(32) NOT NULL,
  `validation_report_json` json NOT NULL,
  `validated_at` datetime NOT NULL,
  `created_by` varchar(128) NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_robot_version_no` (`app_id`,`version_no`),
  UNIQUE KEY `uq_robot_version_hash` (`app_id`,`config_hash`),
  KEY `ix_robot_version_draft` (`app_id`,`source_draft_revision`),
  KEY `ix_robot_version_config_hash` (`config_hash`),
  CONSTRAINT `robot_app_version_ibfk_1` FOREIGN KEY (`app_id`) REFERENCES `robot_app` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `robot_release` (
  `id` varchar(40) NOT NULL,
  `app_id` varchar(40) NOT NULL,
  `robot_app_version_id` varchar(40) NOT NULL,
  `updated_by` varchar(128) NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `app_id` (`app_id`),
  KEY `robot_app_version_id` (`robot_app_version_id`),
  CONSTRAINT `robot_release_ibfk_1` FOREIGN KEY (`app_id`) REFERENCES `robot_app` (`id`),
  CONSTRAINT `robot_release_ibfk_2` FOREIGN KEY (`robot_app_version_id`) REFERENCES `robot_app_version` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `robot_config_audit_log` (
  `id` varchar(40) NOT NULL,
  `actor` varchar(128) NOT NULL,
  `action` varchar(64) NOT NULL,
  `object_type` varchar(64) NOT NULL,
  `object_id` varchar(40) NOT NULL,
  `app_version_id` varchar(40) DEFAULT NULL,
  `config_hash` varchar(71) DEFAULT NULL,
  `detail_json` json NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `outbound_task` (
  `id` varchar(40) NOT NULL,
  `customer_task_id` varchar(128) NOT NULL,
  `name` varchar(255) NOT NULL,
  `robot_app_id` varchar(40) DEFAULT NULL,
  `robot_version_id` varchar(40) DEFAULT NULL,
  `config_hash` varchar(71) DEFAULT NULL,
  `status` varchar(32) NOT NULL,
  `health_status` varchar(32) NOT NULL,
  `timezone` varchar(64) NOT NULL,
  `default_country_code` varchar(8) NOT NULL,
  `scheduled_start_at` datetime DEFAULT NULL,
  `scheduled_end_at` datetime DEFAULT NULL,
  `calling_windows_json` json NOT NULL,
  `excluded_dates_json` json NOT NULL,
  `max_concurrent_calls` int NOT NULL,
  `calls_per_minute` int NOT NULL,
  `next_dial_at` datetime DEFAULT NULL,
  `last_dispatched_at` datetime DEFAULT NULL,
  `retry_policy_json` json NOT NULL,
  `callback_url` varchar(2048) DEFAULT NULL,
  `callback_secret_ref` varchar(255) DEFAULT NULL,
  `state_version` bigint NOT NULL,
  `result_revision` bigint NOT NULL,
  `last_error_code` varchar(64) DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `started_at` datetime DEFAULT NULL,
  `completed_at` datetime DEFAULT NULL,
  `created_by` varchar(128) NOT NULL,
  `origin` varchar(32) NOT NULL DEFAULT 'outbound',
  PRIMARY KEY (`id`),
  UNIQUE KEY `customer_task_id` (`customer_task_id`),
  KEY `robot_app_id` (`robot_app_id`),
  KEY `robot_version_id` (`robot_version_id`),
  CONSTRAINT `outbound_task_ibfk_1` FOREIGN KEY (`robot_app_id`) REFERENCES `robot_app` (`id`),
  CONSTRAINT `outbound_task_ibfk_2` FOREIGN KEY (`robot_version_id`) REFERENCES `robot_app_version` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `task_contact` (
  `id` varchar(40) NOT NULL,
  `task_id` varchar(40) NOT NULL,
  `customer_contact_id` varchar(128) NOT NULL,
  `import_seq` bigint NOT NULL,
  `phone_ciphertext` blob NOT NULL,
  `phone_last4` varchar(4) NOT NULL,
  `phone_hmac` varchar(64) NOT NULL,
  `hmac_key_version` varchar(64) NOT NULL,
  `variables_ciphertext` blob NOT NULL,
  `encryption_key_version` varchar(64) NOT NULL,
  `status` varchar(32) NOT NULL,
  `retry_count` int NOT NULL,
  `active_call_id` varchar(40) DEFAULT NULL,
  `next_call_at` datetime DEFAULT NULL,
  `final_result_code` varchar(64) DEFAULT NULL,
  `last_error_code` varchar(64) DEFAULT NULL,
  `result_revision` bigint NOT NULL,
  `state_version` bigint NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  `finished_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_contact_customer_id` (`task_id`,`customer_contact_id`),
  UNIQUE KEY `uq_contact_phone_hmac` (`task_id`,`phone_hmac`),
  UNIQUE KEY `uq_contact_import_seq` (`task_id`,`import_seq`),
  KEY `ix_contact_task_dial` (`task_id`,`status`,`next_call_at`,`import_seq`,`id`),
  KEY `ix_contact_status_dial` (`status`,`next_call_at`),
  KEY `ix_contact_task_active_call` (`task_id`,`active_call_id`),
  CONSTRAINT `task_contact_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `outbound_task` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `outbound_call` (
  `id` varchar(40) NOT NULL,
  `task_id` varchar(40) NOT NULL,
  `contact_id` varchar(40) NOT NULL,
  `call_no` int NOT NULL,
  `status` varchar(32) NOT NULL,
  `result_code` varchar(64) DEFAULT NULL,
  `retryable` tinyint(1) DEFAULT NULL,
  `robot_version_id` varchar(40) NOT NULL,
  `config_hash` varchar(71) NOT NULL,
  `adapter_boot_id` varchar(40) DEFAULT NULL,
  `reserved_until` datetime DEFAULT NULL,
  `origination_uuid` varchar(40) NOT NULL,
  `freeswitch_uuid` varchar(40) DEFAULT NULL,
  `claim_generation` int NOT NULL,
  `session_state` varchar(32) NOT NULL,
  `session_ready_at` datetime DEFAULT NULL,
  `dial_phase` varchar(32) NOT NULL,
  `dial_requested_at` datetime DEFAULT NULL,
  `dial_acknowledged_at` datetime DEFAULT NULL,
  `check_deadline_at` datetime DEFAULT NULL,
  `answered_by_human` tinyint(1) DEFAULT NULL,
  `raw_hangup_cause` varchar(64) DEFAULT NULL,
  `hangup_source` varchar(64) DEFAULT NULL,
  `failure_code` varchar(64) DEFAULT NULL,
  `failure_message` varchar(1024) DEFAULT NULL,
  `termination_reason` varchar(64) DEFAULT NULL,
  `reviewed_by` varchar(128) DEFAULT NULL,
  `reviewed_at` datetime DEFAULT NULL,
  `review_evidence_ref` varchar(255) DEFAULT NULL,
  `review_note` text,
  `state_version` bigint NOT NULL,
  `created_at` datetime NOT NULL,
  `dialing_at` datetime DEFAULT NULL,
  `ringing_at` datetime DEFAULT NULL,
  `answered_at` datetime DEFAULT NULL,
  `conversation_started_at` datetime DEFAULT NULL,
  `ended_at` datetime DEFAULT NULL,
  `duration_sec` int DEFAULT NULL,
  `mock_browser_uuid` varchar(40) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_call_contact_no` (`contact_id`,`call_no`),
  UNIQUE KEY `uq_call_origination_uuid` (`origination_uuid`),
  UNIQUE KEY `uq_call_freeswitch_uuid` (`freeswitch_uuid`),
  KEY `robot_version_id` (`robot_version_id`),
  KEY `ix_call_status` (`status`),
  KEY `ix_call_task_status` (`task_id`,`status`),
  KEY `ix_call_contact_no` (`contact_id`,`call_no`),
  CONSTRAINT `outbound_call_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `outbound_task` (`id`),
  CONSTRAINT `outbound_call_ibfk_2` FOREIGN KEY (`contact_id`) REFERENCES `task_contact` (`id`),
  CONSTRAINT `outbound_call_ibfk_3` FOREIGN KEY (`robot_version_id`) REFERENCES `robot_app_version` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `call_event` (
  `id` varchar(40) NOT NULL,
  `source` varchar(32) NOT NULL,
  `event_id` varchar(255) NOT NULL,
  `event_type` varchar(64) NOT NULL,
  `task_id` varchar(40) NOT NULL,
  `contact_id` varchar(40) NOT NULL,
  `call_id` varchar(40) NOT NULL,
  `occurred_at` datetime NOT NULL,
  `received_at` datetime NOT NULL,
  `payload_json` json NOT NULL,
  `payload_hash` varchar(64) NOT NULL,
  `process_status` varchar(32) NOT NULL,
  `process_owner` varchar(64) DEFAULT NULL,
  `process_lease_until` datetime DEFAULT NULL,
  `process_attempts` int NOT NULL,
  `next_process_at` datetime DEFAULT NULL,
  `processed_at` datetime DEFAULT NULL,
  `last_error` varchar(1024) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_call_event_source_id` (`source`,`event_id`),
  KEY `task_id` (`task_id`),
  KEY `contact_id` (`contact_id`),
  KEY `ix_call_event_process` (`process_status`,`next_process_at`),
  KEY `ix_call_event_call` (`call_id`,`event_type`),
  KEY `ix_call_event_lease` (`process_owner`,`process_lease_until`),
  CONSTRAINT `call_event_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `outbound_task` (`id`),
  CONSTRAINT `call_event_ibfk_2` FOREIGN KEY (`contact_id`) REFERENCES `task_contact` (`id`),
  CONSTRAINT `call_event_ibfk_3` FOREIGN KEY (`call_id`) REFERENCES `outbound_call` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `call_blacklist` (
  `id` varchar(40) NOT NULL,
  `phone_ciphertext` blob NOT NULL,
  `encryption_key_version` varchar(64) NOT NULL,
  `phone_hmac` varchar(64) NOT NULL,
  `hmac_key_version` varchar(64) NOT NULL,
  `source` varchar(64) NOT NULL,
  `reason` varchar(1024) DEFAULT NULL,
  `effective_at` datetime NOT NULL,
  `expires_at` datetime DEFAULT NULL,
  `created_by` varchar(128) NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_blacklist_hmac` (`phone_hmac`,`hmac_key_version`),
  KEY `ix_blacklist_expiry` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `webhook_delivery` (
  `id` varchar(40) NOT NULL,
  `event_id` varchar(255) NOT NULL,
  `aggregate_type` varchar(32) NOT NULL,
  `aggregate_id` varchar(40) NOT NULL,
  `aggregate_revision` bigint NOT NULL,
  `task_id` varchar(40) NOT NULL,
  `contact_id` varchar(40) DEFAULT NULL,
  `call_id` varchar(40) DEFAULT NULL,
  `event_type` varchar(64) NOT NULL,
  `endpoint_url` varchar(2048) NOT NULL,
  `payload_json` json NOT NULL,
  `status` varchar(32) NOT NULL,
  `delivery_owner` varchar(64) DEFAULT NULL,
  `delivery_lease_until` datetime DEFAULT NULL,
  `attempt_count` int NOT NULL,
  `next_delivery_at` datetime DEFAULT NULL,
  `last_http_status` int DEFAULT NULL,
  `last_error` varchar(1024) DEFAULT NULL,
  `delivered_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_webhook_event_id` (`event_id`),
  UNIQUE KEY `uq_webhook_aggregate_revision` (`event_type`,`aggregate_type`,`aggregate_id`,`aggregate_revision`),
  KEY `contact_id` (`contact_id`),
  KEY `call_id` (`call_id`),
  KEY `ix_webhook_delivery_due` (`status`,`next_delivery_at`),
  KEY `ix_webhook_delivery_lease` (`delivery_owner`,`delivery_lease_until`),
  KEY `ix_webhook_delivery_task` (`task_id`,`created_at`),
  CONSTRAINT `webhook_delivery_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `outbound_task` (`id`),
  CONSTRAINT `webhook_delivery_ibfk_2` FOREIGN KEY (`contact_id`) REFERENCES `task_contact` (`id`),
  CONSTRAINT `webhook_delivery_ibfk_3` FOREIGN KEY (`call_id`) REFERENCES `outbound_call` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `scheduler_state` (
  `id` int NOT NULL,
  `next_dial_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `ck_scheduler_state_singleton` CHECK ((`id` = 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `outbound_call_analysis` (
  `id` varchar(40) NOT NULL,
  `call_id` varchar(40) NOT NULL,
  `business_result` varchar(255) DEFAULT NULL,
  `analysis_json` json NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `call_id` (`call_id`),
  CONSTRAINT `outbound_call_analysis_ibfk_1` FOREIGN KEY (`call_id`) REFERENCES `outbound_call` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DELIMITER //
CREATE TRIGGER `robot_app_version_no_update` BEFORE UPDATE ON `robot_app_version` FOR EACH ROW SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Fast-Call records are immutable'//
DELIMITER ;

DELIMITER //
CREATE TRIGGER `robot_app_version_no_delete` BEFORE DELETE ON `robot_app_version` FOR EACH ROW SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Fast-Call records are immutable'//
DELIMITER ;

DELIMITER //
CREATE TRIGGER `robot_config_audit_log_no_update` BEFORE UPDATE ON `robot_config_audit_log` FOR EACH ROW SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Fast-Call records are immutable'//
DELIMITER ;

DELIMITER //
CREATE TRIGGER `robot_config_audit_log_no_delete` BEFORE DELETE ON `robot_config_audit_log` FOR EACH ROW SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Fast-Call records are immutable'//
DELIMITER ;
