CREATE TABLE device_enrollments (
    repeater_id VARCHAR(36) PRIMARY KEY REFERENCES repeaters(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expected_node_name VARCHAR(128) NOT NULL,
    expected_pubkey VARCHAR(130) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    consumed_at TIMESTAMP WITH TIME ZONE
);
CREATE TABLE device_credentials (
    repeater_id VARCHAR(36) PRIMARY KEY REFERENCES repeaters(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    csr_public_key_sha256 VARCHAR(64) NOT NULL,
    cert_serial VARCHAR(128) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE
);
