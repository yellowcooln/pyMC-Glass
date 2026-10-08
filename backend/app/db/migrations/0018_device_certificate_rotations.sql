CREATE TABLE IF NOT EXISTS device_certificate_rotations (
    repeater_id VARCHAR(36) PRIMARY KEY REFERENCES repeaters(id) ON DELETE CASCADE,
    request_id VARCHAR(36) NOT NULL,
    credential_token_hash VARCHAR(64) NOT NULL,
    csr_public_key_sha256 VARCHAR(64) NOT NULL,
    previous_cert_serial VARCHAR(128) NOT NULL,
    cert_serial VARCHAR(128) NOT NULL,
    client_cert_pem TEXT NOT NULL,
    ca_cert_pem TEXT NOT NULL,
    issued_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    fingerprint_sha256 VARCHAR(64) NOT NULL,
    node_reported_at TIMESTAMP WITH TIME ZONE,
    node_reported_boot_id VARCHAR(36)
);
