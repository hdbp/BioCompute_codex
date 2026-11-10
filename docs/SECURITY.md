# Security and limitations — BioCompute v0.2

This software is a single-administrator research prototype. Treat it as privileged infrastructure.

## Security implemented

- API routes require an administrator Bearer token; token is randomly generated on first startup and stored with mode 0600.
- Agent inventory announcements use a **separate shared discovery token** and never authorize remote code execution.
- File browsing and scientific sample files are restricted to configured canonical data roots; symlink escape and `..` traversal are rejected.
- Local Nextflow scripts must reside in approved pipeline directories. Explicitly confirmed public Registry imports are cloned from a verified registry identifier into the private pipeline storage directory; Git is invoked without an intervening shell.
- Process execution avoids shell=True for the Nextflow invocation; each run has a dedicated working directory.
- Result artifacts can be downloaded only from the run results directory or its symlinks pointing to files in that same run’s Nextflow work directory; external symlinks and traversal are rejected. HTML downloads do not execute inline.
- Remote candidate records cannot be marked executable through the API.
- Default server binding is loopback; optional Docker Compose also binds to host loopback.

## Important limits

- **There is no per-user RBAC, SSO, audit history, request rate limiting, CSRF-protected cookie sessions, or enterprise credential management.** Do not make this internet-facing.
- Tokens are presented by the administrator and kept in browser sessionStorage. Avoid untrusted browser extensions/XSS; use HTTPS for anything other than loopback.
- A trusted imported Nextflow workflow executes commands under the local execution service's account. Public GitHub source code is not inherently trustworthy; review the exact imported revision before running it. Do not run as root; run an isolated OS account with read-only input mounts wherever practical.
- Worker CPU limit configuration in this version is informational and is **not enforced** through HTCondor or Linux cgroups.
- The run runner is local; it can start concurrent jobs without resource-aware queuing. Restrict user access until a proper scheduler is integrated.
- Cancellation is a best effort. Native Nextflow process groups can be terminated; the quick built-in QC job may finish before its cancellation is processed.
- An arbitrary user-provided script and its runtime may write output outside the run directory. Only import trusted pipeline projects.
- Files may contain sensitive lab data. Do not mount or replicate restricted lab datasets onto personal devices without institutional authorization.
- Data roots do not override OS access permissions. Apply Unix ownership, ACLs, mount policies and filesystem quotas separately.
- Docker deployment is a demonstration configuration, not an assurance of secure workflow sandboxing.

## Suggested deployment

Install on the 32-core lab host as a dedicated, non-root service account. Keep raw datasets mounted read-only where analyses allow it. Bind on `127.0.0.1` and access by SSH tunnel or a verified TLS reverse proxy. Obtain institutional IT/security approval before allowing remote devices or agents.

Store secrets outside of source control; rotate the two token files after a suspected leak. Never commit `state/` or `.env`.
