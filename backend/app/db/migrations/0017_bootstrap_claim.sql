CREATE TABLE IF NOT EXISTS bootstrap_claim (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    created_at TIMESTAMP NOT NULL
);

INSERT INTO bootstrap_claim (id, created_at)
SELECT 1, CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM users)
AND NOT EXISTS (SELECT 1 FROM bootstrap_claim WHERE id = 1);
