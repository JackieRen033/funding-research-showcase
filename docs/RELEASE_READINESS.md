# Publication preparation · 2026-10-23

## Implemented in this revision

Automatic discovery/fee scans now run independently per exchange. Results from a fast exchange can produce complete snapshots while another venue is still scanning. A failed venue cannot block healthy venues. Candidate depth refresh runs on the event_poll_seconds target cadence (default 15 seconds), without waiting for the next full market scan. Each venue shares one locked PublicHTTP client across discovery and depth requests, preserving shared pacing and rate-limit cooldown. No overlapping candidate refresh batches are started.

Scans repeat after a 60-second wait per venue; metadata refreshes every 15 minutes. Failed discovery retries on the following scan. The scan JSONL format is now one record per completed venue scan, with an exchange field; older records may contain all venues. Readers must inspect that field. Old observations still expire. Slow APIs, many candidates and shared pacing can still produce stale snapshots; 15 seconds is a target, not an availability guarantee. No freshness or profit thresholds were relaxed.

The UI reports venue progress. Demo startup uses synthetic manual collection instead of the unsupported live auto-scan endpoint. Synthetic and live databases remain separate.

## Verification

56 offline tests passed on the local Python 3.9 environment on 2026-10-23. New tests verify continued collection and refresh while another exchange is blocked, isolation of venue discovery failure, and serialization of requests through a shared client. Existing tests cover stale-data exclusion, fees, event timing, persistence and lifecycle. The test log is included in the prepared package. The isolated publication copy also passed all 56 tests. Its offline CLI demo generated eight explicitly synthetic snapshots in a temporary database; the temporary data was removed after verification. Updated UI JavaScript passed syntax compilation. Browser verification covered synthetic collection, evidence details, JSONL export and stop; the browser reported no warning/error logs. The demo hides live-only automatic scanning controls. An isolated Python 3.9 environment built and installed the package; the installed CLI demo passed with an explicit configuration path, and all three web assets were present. CI is configured for Python 3.9/3.12. Consult the Actions tab for the status of this curated release.

After explicit owner authorization, a bounded public-API smoke test ran on 2026-10-23 at 15:38 UTC (23:38 Asia/Shanghai). While OKX remained scanning, Gate returned 512 funding observations and completed two candidate refresh batches: two full snapshots at 21.12 seconds and four cumulatively at 34.20 seconds. Both selected markets were CONNECTED; POWR/USDT was rejected for sampled depth and TLM/USDT for net return below the research threshold. Their latest snapshot ages were 4.559 and 0.517 seconds. All test workers stopped after 34.56 seconds, and the temporary database was removed. No orders or private API access occurred. The sanitized summary is included as verification/live-smoke-20261023.json. This validates non-blocking refresh in this short run, not completed OKX coverage, other exchanges, 24-hour uptime or strategy profitability.

No claim of 24-hour stability, optimal trading windows, account-level risk controls or profitability. No orders or private API keys.

## Publication choices and usage limits

- Published without a license grant, as agreed with the owner; reuse terms remain undecided.
- This curated release uses a new publication history and preserves the reviewed implementation.
- Live exchange availability and fee policies must be checked at usage time.
- The running local server, if any, must be restarted to load Python scheduling changes. Browser refresh alone only loads UI changes.

## Packaging

In the private source working copy (the packaging helper is not shipped), `python3 tools/prepare_release.py` builds a new allowlisted directory under release/. It includes code, tests, public configuration, selected docs, the offline test log and a sanitized live-test summary. It excludes runtime databases, raw responses, personal resumes, conversation attachments, local settings and existing Git history. A SHA-256 manifest identifies packaged files. This is a source bundle, not a cloud deployment.
