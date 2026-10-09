# Task8 slice — revision-checked mode control and authoritative readback

This is a working configuration/mode slice of Task8, **not completion of Task8 or M1**. Repeater source is `edb4eea17acd342f4e015458d77eda50a438050c` on the existing fork branch. Policy/key transactions, broader schema redaction and reconciliation remain the next work. No live node was deployed, configured or restarted.

## Delivered behavior

- The real ConfigManager now accepts an optional expected revision. It checks both persisted YAML and shared in-memory state under the existing process-wide write lock before staging or applying changes. A stale revision or pre-existing external file edit is rejected without changing the file/shared state or applying anything live.
- Configuration snapshots read actual persisted YAML, not an echoed desired payload. The revision format is `yaml-sha256-v1` (SHA256 of canonical sorted Unicode YAML). Snapshots are internal, unredacted objects; they are never exported directly.
- Authenticated v2 handlers advertise `config.read` only with a correctly shared ConfigManager, and `set_mode` only with the matching runtime manager. Configuration reads explicitly declare scope `repeater.mode`: saved/configured/effective mode plus canonical revisions, not a pretend complete configuration backup. Unknown/plugin fields, passwords, credentials and key paths are excluded by this allowlisted result schema.
- Remote mode jobs require the readback revision and reuse ConfigManager. Successful results require persisted readback and agreement between the daemon and engine runtime mode, not just a successful save or caller-provided hash. Replay uses the existing execution ledger and cannot repeat the write/reload.
- Save failure is not applied. A saved-but-not-live update reports persisted=true/applied=false/restart-required=true. Failed post-write readback retains known persistence but reports unknown completion. Runtime disagreement cannot report success. A superseding file edit is reported as conflict with actual saved/effective mode and revision.
- The owned-worker guard permits exactly its admitted mode change, not unrelated configuration changes. Malformed configuration errors are sanitized so parser values cannot leak through result messages or logs.

## Real verification

Meaningful RED/GREEN outputs are retained under `glass-revamp-run/lab/task8-*`: missing revision API, external-file stale revision overwrite, absent persisted snapshot, absent v2 mode/config capability, secret-bearing parser errors, lost known persistence after failed readback, false success when runtime paths disagree, and false success after a superseding file edit.

Final isolated Python3.12 run: **173 passed** across new transactions, existing atomic persistence/ACL tests, existing ConfigManager tests, v2 job handler, legacy Glass handler and mirrored contracts. No test exclusions. JUnit: `direct-task7-task8-config-final.xml`. New test lint and strict Node OpenAPI checks passed. Scoped production formatting was AST-equivalent; existing production lint debt was measured against the exact HEAD source: ConfigManager21 and GlassHandler98 diagnostics, zero introduced. This is not a claim that whole-project lint/full M1 suites passed.

Actual candidate HTTPS produced six checks: real configuration query, secret exclusion, acknowledged mode job persisted/applied without restart, actual YAML and separate runtime dictionaries agreeing, actual canonical revision different from caller's old hash, and stale-write refusal with the file unchanged. Final-source output is `task8-config-final-http.log`. The runtime dictionaries are deliberately synthetic/no-radio; no physical RF behavior or live-node parity is claimed. The actual Glass API/queue/HTTPS/result-acceptance path was exercised, not mocked.

## Boundaries

Existing callers without a revision retain their old API behavior; remote v2 mode changes require one. The write lock provides same-process manager serialization, **not a cross-process filesystem compare-and-swap guarantee**. Out-of-process edits already present at admission are detected; an unsynchronized editor racing the replace is not claimed safe. Post-write readback detects a superseding mode edit rather than asserting the earlier save is still current.

Keep ordinary configuration reads separate from secret-bearing backups. This mode-scoped read is not lossless full export/import. No generic configuration JSON, policy sync or key sync was newly enabled over v2 to pretend parity. Full M1 backend/node gates belong after the remaining Task8 work, not after every helper.
