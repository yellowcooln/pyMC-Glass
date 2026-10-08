# Original 20-task revamp ledger — continuation checkpoint

Authoritative specification: 2026-10-07_231824-openhop-glass-revamp.md and all four original audit artifacts. No replacement cards or reconstructed specs. Prior checkpoint-task5-report.md retains exact Tasks1–4 and server evidence. This ledger represents implementation dependencies, not authorization to skip milestone gates. Edges below follow plan milestone ordering and explicit shared service dependencies; query/UI work may overlap M1 only after stable protocol and cannot enable mutations.

| Task | Prerequisite / gate | Current status |
|---|---|---|
|1 baselines/classification|none|Completed, preserved existing branch; prior spec/quality and publication evidence retained|
|2 quality gates/fixtures|1|Completed baseline gates; four characterized frontend defects deliberately remain10/16|
|3 v2 contracts/adapters|2|Completed M0 protocol checkpoint; preserve capability fail-closed behavior|
|4 enrollment/authenticated inform|3|Completed isolated HTTPS/PG gate; original lab runtime remains Task4|
|5 MQTT ownership/rotation|4|Partial: stable-ID ingress/materialization, serverCSR/report and inactive durable node pendingCSR source; activation/install/reportproducer/realbroker still pending|
|6 durable claiming/admission|4/3; M1 completion also requires5|Unstarted; typed leases/PostgreSQL claims/admission/result acceptance needed|
|7 node ledger/restart reconciliation|6 command lifecycle/schema|Unstarted; persistence/exactack/kill tests needed|
|8 transactional config/policy/key|6/7 outcome and revision contracts|Unstarted; last-good metadata/state and canonical readback gates needed|
|9 bounded domain queries|3/4 stable authenticated protocol; read-only can overlap5–8|Unstarted; delegate each domain separately|
|10 resource state/navigation|2/3 DTOs; use9 for actual domains|Unstarted; partial-loading characterized defect remains|
|11 read-only domain screens|9/10|Unstarted; no decorative actions|
|12 radio/mesh editor|M1(4–8),9–11 relevantdomain|Unstarted; explicit multi-radio/scoped RF validation|
|13 sensor/GPS/broker editor|M1,9–11 relevantdomains|Unstarted; split independent domains and secret-preserve semantics|
|14 identity/ACL/room/companion|M1,9–11 relevantdomains|Unstarted; reads/RF/secrets separate|
|15 plugin durable lifecycle|M1,9–11 plugins|Unstarted; provenance and unknown completion gates|
|16 policy reconciliation UX|8/10, retained policy fixtures from2|Unstarted; lossless unknown-field/action editor repair needed|
|17 maintenance/update/restore|M1,7/8,9–11 maintenance|Unstarted; actual isolated restore/readiness gate|
|18 fleet rollouts|M1 and relevant12–17 single-node actions|Unstarted; stop-on-failure/expiry/partial outcomes|
|19 typed telemetry/history|3/5 identity contracts, relevant9/domain schemas; M4 after jobs/actions|Unstarted; attribution/dedup/overflow/load limits|
|20 platform/upgrade operations|Finalize across M0–M4, early isolation/bootstrap already established|Partial early groundwork only; locked migrations/worker ownership/finalupgrade/restore/load/browser gates outstanding|

## Task5(c1) fresh delegated scope and gates

Implementer deleg_193cc4c6 delivered ONLY rotation_state.py/test_glass_rotation_state.py/additive README. Parent settled exact schema/API/security limits before delegation in node-rotation-state-decisions.md. Parent specPASS recorded in node-rotation-state-spec-review.md. Independent quality deleg_5adf39c3 APPROVED source-only; no activation/install/broker claims. Parent added a separate simultaneous-eight-process acceptance probe because the implementer's sequential communicate calls were not process contention proof. That probe actually passed in secure CT301.

Parent final actual secure CT301 network-none/read-onlysource/Python3.12 fullnode suite:2821passed,20subtests,1inapplicable-directoryFIFO skip; no test exclusions. JUnit2842 logicaltests,0errors,0failures,1skip;2822 testcaseelements (subtests accounted separately). Retained XML at Repeater worktree .hermes/rotation-state-full.xml (never staged). Existing cache0775 ancestry remains unchanged; publichelper appropriately failsclosed there. Newmodule/tests check-onlyRuff and format plus strictOpenAPI/diff checks pass. Hashes of all4 changedsourcepaths match tested guest candidates. No actualhandler/network/install/reportoutbox implementation in this slice; currentvalid enrollment only, expired recovery future explicitloader.

Independent source quality review approved before fullsuite. Publication verified: Repeater257db7d7495da411c14fef6016a58a95e4b91645 on yellowcooln/openHop_Repeater refs/heads/feat/glass-revamp. Exact remote SHA and GitHub author/committer yellowcooln checked. No failed command approvals in this slice, no approvalsafety changes, no liveRF/production/PR/release. Publication remains parent-owned exactfork only.

## Next concrete task boundary

Define installation state and pending request/key loader contract before a fresh bounded implementer. Preserve pending key/request through lost HTTP responses; validate strict renewal response/DERfingerprint and original CA trust; atomic installed credential bundle/readback; preserve lastgood credentials. Only then implement current successfulMQTTcallback-bound durable reportoutbox and exactacceptedID/serial/state acknowledgment. Broker DB-authoritative ACL/CRL publication/reload and already-connected denial/reconnect proof remain separate real-lab gates. Do not conflate server issuance, node assertion or source tests with broker-authoritative installation.
