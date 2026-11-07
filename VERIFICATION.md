# Verification

- Mixed-case protected-directory fixtures passed on Linux; no real credentials were used. This is not a full platform or publication audit.

- Thirteen Python tests passed in a Hermes-compatible dependency overlay, including resumed download, checksum verification, upload reconciliation, loopback TLS and owner-only POSIX permissions for new job and download files under a permissive umask. The system Python run could not import the Desktop API test because FastAPI was absent.
- Hermes plugin validation passed with no warnings. An earlier independent real HTTPS download passed checksum verification and resumed with a 206 response. Functional Desktop tests were previously recorded; native visual review remains open.
- No authenticated production upload, independently verified remote upload checksum, bandwidth guarantee, Windows test, complete release audit or reviewed demonstration is claimed. Existing records and files are not retroactively chmodded.
