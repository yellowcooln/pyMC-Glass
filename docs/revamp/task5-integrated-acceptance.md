# Task5 integrated acceptance: ownership, cycles, handler and broker

Original plan Task5 steps1–5 exercised as one coherent milestone, not another helper-only publication. M1 still requires Tasks6–8; entire20-task revamp NOTcomplete. No live Repeater code deployed, live node commands/config/enrollment/revoke/rotate/RF, or original Glass stack/DB/CA changes. Only isolated synthetic CT301 project glass-revamp-test candidate changed; new synthetic database rotationlab_task5 preserves earlier lab database and existing lab CA.

## Source

Node uses existing pendingV1/V2/installed/completed/outbox/accepted formats. Strict before/after-cutover/predecessor/current-completed phases bind actual current bytes, key/CSR, identity/origin/token generation/path, pinned CA, prior completion digest, pending/request and journal candidate. Current/replacement crypto and validity remain strict; only protected historical snapshots may expire. Atomic oldaccepted retirement is after durable predecessor proof and before newcredential replacement, with no outbox; completed replacement requires exact newaccepted state. Handler/transport consume successive cycles, exact current Paho callbacks, HTTPS report ACKs and guarded recovery without deleting newer pending state.

Concrete parent-found deadlock fixed: lost successor issuance response on newboot previously queued an OLD-leaf report the real server had already superseded, making installer permanently wait for an unacknowledgeable outbox. Valid successorpending now defers NEW old-leaf reports under filelock; existing durable reports remain protected. Current telemetry unaffected; actual new-current proof/report needed after cutover.

Glass DB-authoritative bounded ACL/CRL publisher, logical cache/CRL refresh and atomic generation publication added. Only broker subdirectory mounted into broker, NOT CA signing key/backend private key. Serverkey0640/brokerGID1883, serviceUID/GID1883 actual. ACL changes HUP own child, CRL changes restart own child to terminate existing revoked-serial TLS sessions; briefly disconnects ALLclients, not falsely atomic with DB. Source config defaultflagFalse/Compose explicitlyTrue, startup after migrations, no Docker socket/host command API. Real Mosquitto PIDfile lacks newline: parent observed actual unhealthy despite passing TLS, fixed safe EOF read; numeric>1/live PID still mandatory, missing/empty/invalid/dead still fail. Actual rebuilt broker healthy.

## Real execution (not mocked Paho/HTTPS)

Existing CA + nginx trusted HTTPS + candidate FastAPI + PostgreSQL + Mosquitto2.1.2:

* Fresh synthetic admin/discovery/adoption, two CSR-key-owned node enrollments via actual HTTPS.
* Actual backend client reads glass/#; device A publishes own stable UUID topic; cross-B publish gets denial; backend publish denied. Device SUBACK1 is allowed by Mosquitto but read ACL suppresses delivery, confirmed against backend-delivered control. Do NOTclaim denied SUBACK.
* Two actual source handler cycles: CSR issuance, private atomic same-path replacement, actual new-current TLS callback/fingerprint, exact HTTPS report/ACK, completion and matched pending retirement. Second issuance rate window intentionally advanced ONLYsyntheticDB timestamps, real issuer/CA/certificates/HTTP unchanged.
* Third issuance actual HTTPS response discarded at test-client boundary after actualservercommit: old current preserved, newboot OLDreport deferred, same persisted request/key recovered and installed. Initial combined harness then failed from a wrong module alias before report step, not product failure; subsequent actual report-loss harness deliberately discarded real committed report response, retained outbox, created NEWhandler boot with actual TLS callback, replayed exact original report/ACK and completed/retired state. This is client-boundary response-loss injection, not a real dropped TCPpacket or OS processkill test.
* Specific previous Aserial fixtureDB revocation (NOTnew public certificate-revokeAPI): already-connected oldserial session terminated by CRL-triggered broker restart. First test attempted during listener restart and got ConnectionRefused (not a denial proof); separate healthy CURRENT-control then confirmed oldserial TLS reconnect denied while CURRENT succeeds.
* Actual admin device-credential revokeHTTP for B: existing Bpublisher terminated, healthy Acontrol succeeds, Bcertificate reconnect denied and Boperational bearer /inform returns401.
* Genuinely expired same-key/same-issuer Bclient leaf signed using existing lab CA rejected by actual TLS; wrong private-key/certificate material rejected locally by SSLcontext (do NOTclaim on-wire rejection).
* HTTPS proxy initially returned405 because replaced file bind still held old inode; exact runtime nginx-T identified oldregex. Only isolated edge recreated, then actual renew/report routes passed. Production proxy source paths not changed by this lab fix.

Parent evidence directory: /home/yellowcooln/openhop-dev/glass-revamp-run/lab/
Logs: integrated-https-enroll,integrated-mqtt-owner-final,integrated-https-mqtt-rotation-final,integrated-https-mqtt-second-cycle,integrated-actual-response-loss(partial harness),integrated-actual-report-loss-final,integrated-serial-revocation(partial downtime attempt),integrated-serial-reconnect-final,integrated-device-revocation,integrated-tls-negative-final,integrated-broker-health-rebuild.

## Gates

Parent secure node focus initial10pass; integratedreporting focus110pass plus one obsolete oldretirement assertion corrected to exact completedjournal/pendingSHA/currentrequest, targeted3pass; real walltime protected-history/current-expiry6pass. Independent spec/quality approved, broker source approved, narrow PIDhealth fix approved and17focused tests pass. One complete final node fullrun:3583passed,2directory/FIFO skips,20subtests passed; JUnit3605logicaltests,0errors/failures,1952.165sec. Node fullsource hashes13changedfiles match local except one proven AST-equivalent reporting-test format bridge, no semantic change or repeated fullsuite for formatting.

Backend complete final suite JUnit508tests,0errors/failures,30skips,699.210sec (opt-in PG/rate/role/environment tests retained skips; actual PG/HTTPS/MQTT above separate). Initial400sectooltimeout interrupted preceding attempt, noXML/process, NOTacceptance; reran once as tracked boundedprocess. PIDhealth focused17 includes added tests not necessarily collected in fullrun; no repeated whole crypto for POSIXpatch. Backend candidate5runtime app source hashes equal local. Scoped Ruff lint/format and strict NodeOpenAPI/diff gates pass; handler98preexisting lint findings unchanged/0added. No frontend changed/build claim. No upstream/source release/tag/force push.

## Limits / next

Issuance/installation/server reports still distinct: report node assertion is not broker attestation; actual synthetic test proves its own broker path only, not field nodes. Expired CURRENT operational loader fails closed; explicit operator reenrollment required, no automatic expiry bypass. Broker health PIDliveness alone not ACL/TLS proof. CRLrestart is global brief disruption; bounded observed real enforcement, not zero-delay distributedtransaction. No OS-kill durability acceptance yet (Task7), no automatic upgrades/restores/fleet actions/browser acceptance (later original tasks).

Next exact original dependency: Task6 typed durable command admission/claims/leases/acceptance IDs/PostgreSQL concurrent delivery, then Task7 bounded node execution ledger/reconciliation, Task8 transactional config/policy/key application to close M1. Preserve previous code/source/fork strategy, no new graph, source/lab synthetic only and no live Repeater changes.
