CREATE TABLE device_command_leases (
    id VARCHAR(36) PRIMARY KEY,
    command_id VARCHAR(36) NOT NULL REFERENCES device_commands(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL,
    issued_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    credential_generation VARCHAR(64) NOT NULL,
    CONSTRAINT uq_device_command_lease_attempt UNIQUE(command_id, attempt),
    CONSTRAINT ck_device_command_lease_attempt CHECK(attempt >= 1 AND attempt <= 3),
    CONSTRAINT ck_device_command_lease_times CHECK(expires_at > issued_at)
);
CREATE INDEX ix_device_command_leases_command_id ON device_command_leases(command_id);
INSERT INTO device_command_leases (id, command_id, attempt, issued_at, expires_at, credential_generation)
SELECT lease_id, id, attempt, lease_issued_at, lease_expires_at, credential_generation
FROM device_commands
WHERE lease_id IS NOT NULL AND attempt >= 1 AND attempt <= 3
AND lease_issued_at IS NOT NULL AND lease_expires_at > lease_issued_at;
ALTER TABLE device_command_receipts ADD COLUMN disposition VARCHAR(16) NOT NULL DEFAULT 'accepted' CHECK(disposition IN ('accepted','superseded'));
