# Changelog

## v0.2.0 — Pipeline Studio and Registry

- Fixed workflow palette drag/drop binding and verified node movement/port connections in a real Chromium DOM harness.
- Added connection-cycle validation and removable draft edges.
- Added Pipeline Studio **Execution & outputs**, with run selection, 4-second refresh, task records, observed Nextflow workspace files, and published files.
- Added `/api/runs/{id}/execution` with bounded trace parser and safe published-output enumeration.
- Added nested downloads and safe handling of Nextflow result links to files in the same run's work directory, rejecting paths outside the run.
- Request Nextflow trace, report, timeline, and DAG artifacts at execution time. Nextflow executable itself was not available during development.
- Added paginated online Nextflow Registry discovery, local keyword search, and confirmed import of published GitHub repositories. The importer clones the default branch and records its revision; release pinning remains future work.
- Added run-detail shortcut to execution visualization.
- Added automated tests of the trace/provenance view, nested output downloads, symlink containment, and registry pagination parser.
- 13 automated Python tests passed, UI smoke passed, offline Chromium drag/connect/output-visualization interactions passed.

**Out of scope:** Executing visual draft workflows as Nextflow DSL2, Nextflow execution verified on an actual installed runtime, distributed worker job dispatch, exact per-task published-output attribution using Nextflow lineage.
