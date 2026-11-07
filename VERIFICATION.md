# Verification

- A standalone headless browser fixture with approximate theme variables was inspected at 375 px. The initially disabled controls now appear distinct and the page had no horizontal overflow; the Desktop flow test passed. This is not native Hermes Desktop or a complete accessibility review.

- Mixed-case protected-directory fixtures passed on Linux; no real credentials were used. This is not a full platform or publication audit.

- Fourteen Linux Python tests passed in a Hermes-compatible dependency overlay, including resumed download, checksum verification, upload reconciliation, loopback TLS and owner-only POSIX permissions for new job and download files under a permissive umask. The system Python run could not import the Desktop API test because FastAPI was absent.
- Hermes plugin validation passed with no warnings. An earlier independent real HTTPS download passed checksum verification and resumed with a 206 response. Functional Desktop tests were previously recorded; native visual review remains open.
- Native Windows Python 3.14 passed a synthetic loopback TLS upload with an interrupted response, explicit reconciliation, a resumed checksum-verified download, mixed-case private-path rejection and cross-process lock contention. The test used a temporary local CA, not a production service.
- No authenticated production upload, independently verified remote upload checksum, bandwidth guarantee, Windows ACL review, complete release audit or reviewed demonstration is claimed. Existing records and files are not retroactively chmodded.
