CREATE TABLE device_observations (
    repeater_id VARCHAR(36) PRIMARY KEY REFERENCES repeaters(id) ON DELETE CASCADE,
    boot_id VARCHAR(36) NOT NULL,
    sent_at TIMESTAMP WITH TIME ZONE NOT NULL,
    capabilities_json TEXT NOT NULL,
    inventory_json TEXT NOT NULL,
    telemetry_json TEXT NOT NULL
);
