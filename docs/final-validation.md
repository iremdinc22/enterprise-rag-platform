# Final localhost demo validation

[Overview](../README.md) · [Testing guide](testing.md) · [Screenshots](images/README.md)

**Verdict: GO for the localhost portfolio demo.** This is not a production-readiness
assessment or a validation of real LLM answer quality.

## Evidence and scope

[Atlas Demo E2E run 44090728527](https://github.com/iremdinc22/enterprise-rag-platform/actions/runs/44090728527)
completed successfully in **4 minutes 28 seconds**, using a manually triggered
standard GitHub-hosted Ubuntu 24.04 runner. Tested commit:
`626575e1fde6e6a0017995cfd5c80e798566a1bf`.

Earlier local validation was limited by memory pressure on an M2 with 8 GB RAM;
the remaining clean-installation and browser checks therefore ran on Linux.
No confirmed application defects remained in the reviewed evidence.

| Validation stage | Successful results |
| --- | ---: |
| Backend regressions, earlier checkpoint | 274 |
| Generation/retrieval evaluations, earlier checkpoint | 30 |
| Frontend regressions, earlier checkpoint | 35 |
| CI helper/security tests, workflow implementation checkpoint | 18 |
| Controlled Docker E2E scenarios, final CI | 18 |
| Playwright browser E2E tests, final CI | 2 |
| **Consolidated total** | **377** |

These are results from multiple stages, **not 377 tests in one workflow run**.
There were also **12 HTTP control groups** and **nine authorization/session
controls**; these grouped assertions are reported separately. Earlier typecheck,
production build, documentation and static security checks passed. Documentation
finalization did not rerun tests or inference.

## Clean installation and provenance

Fresh application and frontend images were built from the tested checkout in an
isolated Compose project. The controlled-provider environment was cleaned up before
the deterministic demo started. All five demo services became healthy. The frontend
asset hashes and running image identities matched the generated provenance report.

- Application image: `sha256:eab0726d06201d98279692d0fdb798724a7a545e06cc9863a0ae038601b34713`
- Frontend image: `sha256:3fcf09067844628e60209c2a61aa007939a46b6ab0e427da362a91dd1a30e7ae`
- Frontend JavaScript SHA-256: `de564401c868125a5e8c422d6e6828705891105e63b113d99646ea3f3fb80a75`
- Frontend CSS SHA-256: `5844884fd4845bc36c442d02d34c4de77342f5778d695f694d806b3989a95b4b`

## Controlled Docker scenarios — 18/18

The existing controlled-provider suite passed authentication validation; persisted
worker failure; worker retry; citation retry exhaustion; successful citation retry;
concurrent tenants; empty-context error contract; empty-tenant error contract;
sanitized generation failure; header/URL tenant-override protection; invalid stored
citation metadata; cache MISS/HIT/BYPASS and tenant separation; PDF/Celery lifecycle;
external-provider network blocking; Qdrant failure/timeout; hybrid retrieval and
tenant citations; Redis fallback; and replacement cache invalidation.

The empty-context/tenant tests preserve the original backend's current error
contract; they do not imply universal graceful abstention. The deterministic demo
separately demonstrated no-evidence responses.

## HTTP and browser evidence

HTTP checks covered both tenant sessions, unauthenticated rejection, empty-library
abstention, PDF admission, completed Celery jobs, document listing/download bytes,
page-2 citations, idempotent replay/conflict, corrupt-PDF rejection, foreign-job/file
404 consistency, foreign-fact abstention and repeated cached-answer citation validity.
Synthetic ACME Orion evidence returned **731 USD**; GLOBEX Nebula returned **284 USD**.

Authorization checks covered missing login fields, prohibited client claims,
unknown accounts, expired/forged JWTs, tenant override attempts, employee upload
denial, pagination validation and document `no-store` headers.

The two Chromium tests covered initial empty states and the authenticated workflow:
invalid credentials; ACME/GLOBEX login; file-chooser and drag-and-drop uploads;
Ready status; tenant-owned libraries; answers and real page-2 source citations;
authenticated download hash checks; no-evidence/error states; logout, account
switching and suppression of stale session work. Additional synthetic Beacon and
Comet evidence returned 419 USD and 862 USD. No application API responses were mocked.
A controlled browser clock exercised frontend expiration; actual expired JWT
rejection was checked separately over HTTP rather than waiting 15 real minutes.

Responsive checks used **1440×900, 1280×800, 768×1024 and 390×844**, including
horizontal-overflow checks. Reviewed screenshots showed readable desktop/mobile
layouts, preserved simulation labels and no exposed credentials. No unexpected
application console/page errors were reported. Selected originals are preserved in
[the image archive](images/README.md).

## Resources, cleanup and artifact safety

The 39 resource samples reported no safety stop, at least 13.38 GiB available RAM,
at least 84.63 GiB free disk and zero swap usage. Sampling covered the monitored
runtime phase, not every second of the entire workflow.

Scoped cleanup completed successfully. There is no final Docker inventory receipt
proving every temporary image was removed; image removal was best effort on the
disposable runner. Existing local containers, volumes and data were outside this CI
environment. No local Docker was started for the final artifact review.

The reviewed artifact contained eight JSON reports and eight PNGs (approximately
0.97 MB compressed), with no credentials, JWTs, environment files, raw network dumps,
HAR, traces or browser storage exports. Its SHA-256 was
`ccbf71a3dbdd42673ef1bd6195b4d7858c27bbd50fbf287203519f5e1fb1d825`.
Only the six selected screenshots and this summary are added to the repository;
the full sanitized archive is preserved locally. CI artifacts expire after seven days.

The artifact uploader emitted non-blocking Node runtime/deprecation warnings;
upload and all validation steps succeeded. No paid runner or paid inference was
used. The deterministic demo and controlled fake providers required no OpenAI keys.
This review does not audit the owner's complete account-level storage bill.

## Remaining limitations

- Deterministic lexical matching is intentionally limited and does not measure LLM
  quality; paraphrases can produce no-evidence responses.
- Document replacement remains delete-then-upsert, with write-failure, concurrency
  and cache-consistency windows; it is not atomic.
- Query limits are process-local and are not distributed quotas or billing caps.
- Browser evidence covers Linux Chromium and four viewports, not all browsers,
  accessibility technologies or full Docker/macOS compatibility.
- TLS, operational backup/recovery, distributed controls and further deployment
  hardening remain necessary before any public production use.
