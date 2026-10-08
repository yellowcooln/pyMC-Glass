# Original 20-task revamp ledger — continuation checkpoint

Authoritative specification: 2026-10-07_231824-openhop-glass-revamp.md and all four original audit artifacts. No replacement cards or reconstructed specs. Prior checkpoint-task5-report.md retains exact Tasks1–4 and server evidence. This ledger represents implementation dependencies, not authorization to skip milestone gates. Edges below follow plan milestone ordering and explicit shared service dependencies; query/UI work may overlap M1 only after stable protocol and cannot enable mutations.

| Task | Prerequisite / gate | Current status |
|---|---|---|
|1 baselines/classification|none|Completed, preserved existing branch; prior spec/quality and publication evidence retained|
|2 quality gates/fixtures|1|Completed baseline gates; four characterized frontend defects deliberately remain10/16|
|3 v2 contracts/adapters|2|Completed M0 protocol checkpoint; preserve capability fail-closed behavior|
|4 enrollment/authenticated inform|3|Completed isolated HTTPS/PG gate; original lab runtime remains Task4|
|5 MQTT ownership/rotation|4|Partial: stable-ID ingress/materialization, serverCSR/report, durable node pendingCSR/validatedcandidate/offlineatomicbundleinstall; handler/reportproducer/retirement/realbroker still pending|
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

## Task5(c2) offline candidate validator checkpoint (published)

Fresh implementer deleg_075140a0 added only validate_renewal_candidate plus focused tests and additive node README. Parent actual-schema/complete-diff specification review PASS in node-renewal-candidate-spec-review.md preceded fresh independent quality deleg_55cbdbf3 APPROVED (source-only). Contract in node-renewal-candidate-decisions.md: existing pending request/key authority, strict eight-field wire response, bounded canonical single PEM, exact issuing-CA DER pinning, certificate/key/identity/validity/metadata/fingerprint checks and explicit bearer/origin/pubkey preservation. No filesystem creation or credential/pending mutation, networking, installation or activation.

Parent secure CT301 focus167passed2inapplicable-directoryFIFOskips. Fullsuite first completed with8failures because parent-added tmpfs was noexec; actual /proc/mounts proved this and logs/JUnit retained, not accepted. Rerun used the image's existing private0700 directory in disposable container layer, without source changes or security relaxation:2936passed2skipped20subtests; JUnit2958logicaltests0errors0failures2skips,2938testcaseelements. Newmodule/tests Ruff lint/format, strictOpenAPI and diffcheck pass. All3changedfile hashes match actual tested guest candidate. Runtime app remains Task4, original services/DB/CA untouched. Verified node publication: Repeater e6a896ca6faae18aa32d505bad18a74f3e642c7b on exact authorized fork https://github.com/yellowcooln/openHop_Repeater.git refs/heads/feat/glass-revamp; normal push, exact local/remote SHA and GitHub author/committer yellowcooln read back. Glass decision/spec/ledger publication follows.

## Task5(c3) offline atomic installation checkpoint

Fresh implementer deleg_9f25c762 delivered only rotation_state.py, new test_glass_rotation_install.py and additive node README. Parent-owned decisions node-renewal-install-decisions.md and parent specPASS node-renewal-install-spec-review.md preceded independent quality deleg_592968ad APPROVED (read-only, not independent execution). Existing private fixed lock, strict target authority and one immutable bounded secret install.json journal preserve lastgood credentials; validated private staging/snapshot reread/atomic replacement/fsync/fullreadback and old/newcaller retry recover postreplace uncertainty without pretending rollback. No handler/network/MQTT/report or pending retirement.

Parent exact CT301 focus245pass2directoryFIFOskip; complete fullnode3014pass2skip20subtests. Retained JUnit3036logicaltests0errors0failures2skips/3016testcaseelements; source archiveSHA25694f1a3d6d7117801cc5bcc6b803d665f0684f162541277a9be3abf5572317225. All3changedfile hashes guest/local equal; changed lint/format, strictOpenAPI and diffchecks pass. No source exclusions. Child convenience execute_code policy denial recorded once in boardcomment131, no call retried/approvalsettings changed. Original/labTask4 app remains unchanged; these are offline tests, not deployment or broker proof.

Published verified Repeater2fa428362bcdc4120f76d89acd9f8386983bf0a4 at https://github.com/yellowcooln/openHop_Repeater.git refs/heads/feat/glass-revamp, normalpush exactremoteSHA/GitHubauthor+committer yellowcooln verified. c2 intermediatecheckpoint e6a896ca6faae18aa32d505bad18a74f3e642c7b and Glass ca88e1b6e74ce4ab09e13eda3b35f043cc0bd113 also verified. Glass c3 decision/spec/ledger publication follows. No fullTask5/M1/revamp completion.

## Task5(c4) verified HTTPS renewal and handler cutover checkpoint

Fresh implementer deleg_49f6a51d added bounded transport/pendingprobe, enrolled asyncmaintenance and additiveREADME/tests. Parent initial realCT30187focus gate followed test-only canonicalmodule binding repair deleg_a0798bcd: dynamicallyloaded testhandler ignored MQTT/clock mocks; failedlog retained, no livebroker contacted (networknone). ParentSpecPASS in node-renewal-transport-spec-review.md preceded independentquality deleg_d37bab54, which found real cancellation ownership race and withheldapproval. Fresh fixer deleg_8e7cd290 RED4 regressions then shieldedexecutor ownership/drain underfixedasyncLock with repeatedcancel and safe workerfailuredata; parent repairedspecPASS+actual91focuspass; fresh independentquality rereview deleg_2531ea88 APPROVED (source-only). Cancellation never activates abandonedcandidate; publisher invalidated before/afterdrain.

Parent completefinalCT301 node3048passed2directoryFIFOskip20subtests; JUnit3070logicaltests0errors0failures2skips/3050testcaseelements. lab/node-c4-full-final.log/.xml and node-c4-focus-cancel-fixed.log retained; earlier full-beforefix is superseded. Fivechangedfile hashes exactlocal/testedguestmatch. Newfiles Ruff lint/format, strictOpenAPI/diffpass; inheritedhandler98diagnostics before/after, zeroadded code/message findings. Fullrepo lint not claimed clean. Repeater16f02d90a7b4529c5eda4e8c7c78daae994b3a7e normalpush exactauthorizedforkbranch/GitHubauthor+committeryellowcooln verified. Glass c4 decision/spec/ledger publication follows.

This is real syntheticPKI/materialization/atomicfile/callbacksimulation with mocked HTTPS/Paho, NOT realbroker/proxiedHTTPS proof. Current helper/handler supports one pending request: durableCSR->verifiedHTTPSrenew->atomicinstall->fingerprintmaterialize/reconnect, retries installedstate offline and preserves configuredHTTPStrust. Current/previouscertificate validity still required; no expiredloader, reportproducer/ack/retirement or capabilityexpansion. Lab runtime stillTask4 source6069a18; originalDB/CA/services unchanged. FullTask5/M1 incomplete.

## Next concrete task boundary

Parent define durable current-successful-MQTTcallback reportoutbox plus completed-generation retirement schemas BEFORE fresh bounded implementation. Existing c4 renewhelper/handler is now present; extend only currentcallback-publicreport binding and exact delivery acknowledgment. Send publicreport(device/request/serial/fingerprint/boot/connectedliteraltrue), retain until exact accepteddevice/request/serial/acceptedtrue/statenode_reported acknowledgment. Do not retire pending/journal or generate a second key before a durable completed receipt safely binds that acknowledgment and current installed generation; preserve uncertain keys/lastgood backup across crash. Existing immutable oldMQTTdirectories remain recovery assets, not claims of rollback. Expired-current recovery still requires separately defined secure loader. Broker DB-authoritative ACL/CRL publication/reload and already-connected denial/reconnect proof plus actual proxied HTTPS acceptance remain separate mandatory real-lab gates. Do not conflate server issuance, node assertion or source tests with broker-authoritative installation.
