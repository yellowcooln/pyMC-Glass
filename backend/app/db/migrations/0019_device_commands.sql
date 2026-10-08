ALTER TABLE device_observations ADD COLUMN received_at TIMESTAMP WITH TIME ZONE;

CREATE TABLE device_commands (
    id VARCHAR(36) PRIMARY KEY,
    device_id VARCHAR(36) NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
    request_id VARCHAR(36) NOT NULL,
    execution_id VARCHAR(36),
    idempotency_key VARCHAR(64),
    action VARCHAR(64) NOT NULL,
    request_json TEXT NOT NULL,
    request_sha256 VARCHAR(64) NOT NULL,
    requester_user_id VARCHAR(36) NOT NULL REFERENCES users(id),
    requested_by VARCHAR(128) NOT NULL,
    credential_generation VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    lease_id VARCHAR(36),
    lease_issued_at TIMESTAMP WITH TIME ZONE,
    lease_expires_at TIMESTAMP WITH TIME ZONE,
    attempt INTEGER NOT NULL DEFAULT 0,
    completed_at TIMESTAMP WITH TIME ZONE,
    result_json TEXT,
    result_sha256 VARCHAR(64),
    acceptance_id VARCHAR(36),
    persisted BOOLEAN,
    applied BOOLEAN,
    restart_required BOOLEAN,
    error_code VARCHAR(64),
    superseded_by VARCHAR(36) REFERENCES device_commands(id),
    CONSTRAINT uq_device_commands_request UNIQUE(device_id, request_id),
    CONSTRAINT uq_device_commands_idempotency UNIQUE(device_id, idempotency_key),
    CONSTRAINT uq_device_commands_execution UNIQUE(device_id, execution_id),
    CONSTRAINT ck_device_commands_attempt CHECK(attempt >= 0 AND attempt <= 3),
    CONSTRAINT ck_device_commands_status CHECK(status IN ('queued','received','running','awaiting_verification','succeeded','failed','expired','cancelled','unknown'))
);
CREATE INDEX ix_device_commands_device_id ON device_commands(device_id);
CREATE INDEX ix_device_commands_status ON device_commands(status);

CREATE TABLE device_command_receipts (
    acceptance_id VARCHAR(36) PRIMARY KEY,
    command_id VARCHAR(36) NOT NULL REFERENCES device_commands(id) ON DELETE CASCADE,
    result_json TEXT NOT NULL,
    result_sha256 VARCHAR(64) NOT NULL,
    lease_id VARCHAR(36) NOT NULL,
    attempt INTEGER NOT NULL,
    accepted_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT uq_device_command_receipt_digest UNIQUE(command_id, result_sha256)
);
CREATE INDEX ix_device_command_receipts_command_id ON device_command_receipts(command_id);
