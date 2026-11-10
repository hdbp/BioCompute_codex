# BioCompute v0.2 architecture

```
Mac / browser ------ SSH or approved TLS transport ---------+
                                                          |
                                               FastAPI controller
                                               localhost:8000
                                                          |
                   +----------------+-----------------------+----------------+
                   |                |                       |                |
               SQLAlchemy      File-browser             Local runner     Discovery
               SQLite/PG       allowlisted roots          queue           inventory
                   |                |                       |                |
            experiments          FASTQ/BAM                +-----------+     local psutil
            presets            data paths                |           |     neighbor cache
            jobs                validation                |           |     Tailscale CLI
            nodes                                      Python QC     Nextflow CLI  condor_status
            graph drafts                              (tested)      (not verified) agent heartbeat
                                                         |           |          |
                                                  CSV / HTML / JSON    candidates only
                                                       results
```

## Trust boundaries

1. A single web admin token protects the API. Do not expose FastAPI publicly.
2. Worker inventory has its own token and never authorizes command execution.
3. Approved filesystem roots constrain browsing and scientific sample inputs, but not the commands in trusted Nextflow pipelines. Run as a least-privileged OS account.
4. Imported pipelines are executable code, not untrusted documents.
5. Remote devices are discovery candidates. No remote task submission exists in this version.

## Data structures

- `Pipeline`: engine, project location, pipeline version, full JSON schema.
- `Experiment`: name, assay and sample dictionaries, including custom per-sample metadata.
- `Run`: immutable run request snapshot with pipeline ID, input sample manifest, parameters, status, paths, timings and error.
- `Node`: discovered hardware, source, status, policies, actual executor eligibility.
- `Preset`: named parameter configuration.
- `Graph`: editable visual draft layout and edges.

This milestone creates tables with SQLAlchemy `create_all`; migrations and versioned schema evolution need Alembic in a subsequent release.

## Execution lifecycle

1. The web UI sends an authenticated `POST /api/runs` request with a pipeline ID, sample manifest and parameters.
2. The API validates sample paths against approved roots, checks duplicate/unsafe sample IDs, validates common JSON-schema parameter types and required samplesheet columns, and rejects remote worker selection.
3. The API persists a queued run, and the in-process runner updates it to running. Run folders are created in `state/runs/<UUID>/`.
4. Built-in QC reads actual FASTQ input files. Nextflow runs use a subprocess and a generated params JSON / samplesheet CSV.
5. Output, stderr/stdout and final status are stored on disk and in the database.
6. The UI polls status, logs by incremental byte offset, Nextflow trace task records and authenticated downloadable results (including nested outputs). Pipeline Studio shows each observed process stage and resulting files.
7. Runs found queued/running after controller restart are marked interrupted rather than falsely reported active.

## Next milestones

1. Worker enrollment with mTLS identities or signed machine certificates; agent attestation and scheduler integration.
2. Resource-aware dispatch through HTCondor, respecting CPU/RAM limits and data egress restrictions.
3. Schema-aware samplesheet model enhancements for proteomics and complex nf-core workflows.
4. React/TypeScript frontend migration and accessible React Flow canvas; typed API client.
5. Validated Nextflow DSL2 code generation from a constrained visual graph with versioned modules and container metadata.
6. PostgreSQL Alembic migrations, RBAC, audit logs, service health probes, rate limiting and production deployment verification.
7. Full controller-integrated Playwright browser E2E coverage in an environment that permits local browser navigation (the UI drag/connect and output-display flows were tested with an offline DOM harness).
