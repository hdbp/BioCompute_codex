# BioCompute v0.2.0 · Scientific Compute Command Center

**An installable, offline-capable first milestone of BioCompute** — an authenticated web application for scientists to browse data, configure samples and Nextflow pipelines, launch real local workflows, monitor logs, and discover candidate computers.

> **Scope:** This release is an executable **local-controller MVP**, not a finished multi-node HPC system. See [Implementation status](#implementation-status) before placing it on an institutional network.

## Screens

- **Overview** — real local node inventory, CPU/RAM utilization, pipeline and job summaries.
- **Compute Nodes** — live controller, passive LAN-neighbor/Tailscale/HTCondor discovery, manual candidates, signed inventory-only worker heartbeats, local resource policy controls.
- **Pipeline Library** — built-in FASTQ analysis, Nextflow DSL2 example, import approved local projects **or search/import all currently published pipelines in the paginated Nextflow Registry**. Render parameter forms from JSON schema (including `$defs`).
- **Experiments** — sample/condition/replicate/batch table, permitted file browser, CSV/TSV import, saved experiments, pipeline-specific metadata columns.
- **Run Manager** — real local processes, run history, incremental logs, downloadable nested CSV/JSON/HTML result artifacts, cancel Nextflow runs, open execution details in Pipeline Studio.
- **Pipeline Studio** — **fixed working drag/drop and port-to-port connections** for saved drafts; new **Execution & outputs** view shows processing stages, task statuses, observed task-workspace files, and downloadable published outputs. A visual draft is **not** yet runnable or compilable to DSL2.
- **Settings** — runtime readiness, approved data paths, execution and security limitations.

No fake completion states: the included FASTQ QC pipeline **actually reads files** and calculates per-file reads, mean length, mean Phred+33 quality, and GC%. It writes reports in the run directory.

## Updating an existing v0.1.0 installation (your existing Linux directories)

If you installed the original archive under `~/Downloads/BioCompute-v0.1.0/BioCompute`, with its virtual environment one directory above, stop the running BioCompute terminal process (`Ctrl+C`) and use:

```bash
cd ~/Downloads/BioCompute-v0.1.0/BioCompute
cp -a state "state.backup-$(date +%Y%m%d-%H%M%S)"
unzip -o ~/Downloads/BioCompute-v0.2.0.zip -d .
../.venv/bin/python -m pip install -e .
../.venv/bin/python -m uvicorn biocompute.app:app --host 127.0.0.1 --port 8001
```

Refresh the web page at **http://127.0.0.1:8001** with `Ctrl+Shift+R`. The update archive excludes existing database and token files. Your previous saved data remains in `state/`.

## Quick start on your 32-core Linux lab workstation

Prerequisites: **Python 3.11+** and the ability to read data folders. No Node/npm installation or external frontend CDN is required.

```bash
unzip BioCompute-v0.2.0.zip -d BioCompute
cd BioCompute
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'

# Optional: add your real lab data directory before startup.
export BIOCOMPUTE_DATA_ROOTS=/mnt/lab/raw_data

.venv/bin/python -m biocompute.app
```

From the same computer, open **http://127.0.0.1:8000**. A random administrator token is created during first startup:

```bash
cat state/admin-token.txt
```

Paste that token into the login screen. The token is not bundled with the source archive. Without additional configuration, the file browser includes the bundled `examples/data` sample files.

### Connect from your Mac without exposing the app publicly

From an authorized Mac workstation, create an SSH tunnel to the lab machine:

```bash
ssh -L 8000:127.0.0.1:8000 YOUR_USER@YOUR_LAB_HOST
```

Then open `http://127.0.0.1:8000` on your Mac and authenticate with the token. A Tailscale hostname can be used as `YOUR_LAB_HOST` if available, but **Tailscale is optional**. This path assumes your institution allows the connection. For a persistent service use a reverse proxy with TLS/authentication and restricted exposure.

### Try the real FASTQ demo

1. Select **Pipeline Library → FASTQ Quality Explorer → Configure**.
2. Continue to **Samples → Example data**. Four small synthetic FASTQ samples are populated with Control/Treatment and two replicates each.
3. Click **Validate samples**, then continue to **Parameters** to modify maximum reads and read length warning threshold.
4. Continue through **Resources → Review → Launch analysis**.
5. In **Run Manager**, open the run to inspect its real log and download `qc_metrics.csv`, `summary.json`, and `report.html`.

The synthetic sample files are for application testing, not biological interpretation.

## Running Nextflow pipelines

The code includes a **real Nextflow local-execution adapter**. For real Nextflow execution, install Nextflow and Java on the controller and make `nextflow` discoverable on `PATH` for the BioCompute service user. Check with:

```bash
nextflow -version
java -version
```

BioCompute invokes `nextflow run <approved-local-pipeline> -params-file <run-params.json> -work-dir <run-work> -with-trace <run-trace>`. It does **not** silently replace missing Nextflow with a mock. The adapter requests trace, timeline, report, and workflow DAG files. **Nextflow itself was not installed in the authoring environment**, so successful Nextflow execution and report generation remain unverified here.

Nextflow pipelines can be imported from a directory containing `main.nf` plus optional `nextflow_schema.json` and `assets/schema_input.json`. To allow import from a folder:

```bash
export BIOCOMPUTE_PIPELINE_ROOTS=/mnt/lab/pipelines
```

The schema-driven editor supports ordinary JSON schema types, enums, default values, ranges, `$defs` and `definitions`. A generic file path field is shown for pipelines without a schema. Additional required sample metadata is rendered when `assets/schema_input.json` is present.

**Important:** Not all nf-core pipelines share the same input columns or reference/configuration requirements. Schema compatibility is a first implementation, not a guarantee that every imported pipeline can execute unchanged. Validate with a small test dataset before real work. Nextflow scripts are executable code and must come from trusted sources.

## Online Nextflow Registry catalog

Open **Pipeline Library → Nextflow Registry**. BioCompute queries the public paginated listing at `https://registry.nextflow.io/pipelines` and locally searches every published page. The latest published versions/descriptions are displayed alongside an **Import** control.

- Registry discovery requires network access from the controller to `registry.nextflow.io`.
- Import downloads the selected workflow's public GitHub repository into `state/pipelines/`. Git must be installed and the controller must be able to reach GitHub.
- The current importer clones the repository's default branch (not necessarily the displayed Registry release). BioCompute captures its exact Git commit in the imported description, so inspect that commit before executing. Release-aware version pinning is still planned.
- Imports require an explicit confirmation, and they **never run** pipeline code during import. Treat all imported Nextflow workflows as executable, untrusted third-party code until reviewed.
- Registry HTML and repo layout can change; this version uses a bounded standard-library HTML parser rather than an undocumented Registry API. Automated tests use representative cached HTML; live registry browsing was not tested in the offline development environment.

## Pipeline execution stages and generated files

Open **Pipeline Studio → Execution & outputs**, select a run, and monitor it. The view polls the controller every four seconds and shows:

- Actual task names, statuses and duration from `results/trace.txt` for Nextflow runs.
- Observed files in each task's workspace (`work/`), where metadata is available; these may include copied inputs rather than only new outputs.
- Recursively enumerated **published results** under the run's `results/` directory, with sizes and authenticated downloads, including Nextflow links back to files within that run's `work/` tree.
- For the built-in FASTQ QC engine, one completed processing stage plus its genuine `qc_metrics.csv`, `summary.json`, and `report.html` outputs.

Task trace records may be delayed or incomplete while Nextflow is still running. The GUI never infers which published file came from which specific task unless provenance supports it. Nextflow's optional data-lineage feature could support richer process-to-output tracking later.

## Discover candidate computers

**No wide LAN sweep is performed.** A refresh currently combines:

- The controller's own CPU/RAM/OS inventory (`psutil`).
- Passive kernel neighbor cache (`ip -j neigh show` where available).
- Tailnet devices (`tailscale status --json` if installed).
- Existing HTCondor execution nodes (`condor_status -json` if installed).
- Signed BioCompute worker-agent heartbeats.
- Explicitly added hostname/address candidates.

All candidates remain **non-executable** until a later milestone supplies verified worker enrollment, HTCondor integration and authorized data policies. Presence on a network is not permission to run code.

### Run an inventory-only agent on a Mac or Linux worker

Install the same BioCompute package on a candidate computer and supply the controller URL and **separate agent token**. For remote machines use an HTTPS controller address with authenticated access, or an authorized private test network for initial setup.

On the controller, obtain the discovery token:

```bash
cat state/agent-token.txt
```

On the candidate machine:

```bash
export BIOCOMPUTE_CONTROLLER_URL=https://YOUR-CONTROLLER-HOSTNAME
export BIOCOMPUTE_AGENT_TOKEN=PASTE_SEPARATE_AGENT_TOKEN
python3 -m biocompute.worker_agent
```

This agent reports a stable machine ID, OS, CPU, RAM and current load. It **does not** install software, accept commands or execute jobs. The agent token is distinct from the web administration token. For loopback HTTP testing only, set `BIOCOMPUTE_ALLOW_INSECURE_HTTP=1` deliberately; never use this for untrusted networks.

## Running tests

```bash
.venv/bin/python -m pytest -q tests
node --check frontend/app.js  # optional, if Node is installed
node tests/ui_smoke.cjs     # optional static UI render smoke test
```

The test suite covers authentication, approved root containment, symlink escape prevention, sample/parameter validation, real QC outputs, artifact authentication, passive node inventory safeguards, graph persistence, and signed heartbeat behavior.

## Deployment choices

**Recommended for real Nextflow pipelines:** native Python service on the lab Linux PC, running as a dedicated non-root service account with precisely scoped data permissions. See `docs/systemd/biocompute.service.example`.

**Optional containerized API:** `docker compose up --build -d` with a PostgreSQL database. Copy `.env.example` to `.env` and supply a strong database password. The Docker Compose example does **not** install Nextflow or grant host execution-runtime access; the built-in FASTQ QC path is the supported container smoke test. Nextflow requires a deliberately provisioned runner outside this base Compose setup. The container ports bind to loopback by default.

All generated run workspaces and logs are under `state/runs` by default. The database is `state/biocompute.sqlite3` for native development or PostgreSQL with the Compose example.

## Configuration

| Environment variable | Function | Default |
|---|---|---|
| `BIOCOMPUTE_STATE_DIR` | Persistent database, tokens, run outputs | `./state` |
| `BIOCOMPUTE_DATA_DIR` | Primary approved data root | `./examples/data` |
| `BIOCOMPUTE_DATA_ROOTS` | Additional approved data roots, colon-separated on Linux | none |
| `BIOCOMPUTE_PIPELINE_ROOTS` | Additional importable pipeline roots | none |
| `BIOCOMPUTE_ADMIN_TOKEN` | Override generated UI token | generated file |
| `BIOCOMPUTE_AGENT_TOKEN` | Override generated worker inventory token | generated file |
| `BIOCOMPUTE_DATABASE_URL` | SQLAlchemy connection URL | SQLite |
| `BIOCOMPUTE_HOST` | Bind address | `127.0.0.1` |
| `BIOCOMPUTE_PORT` | Bind port | `8000` |

## Implementation status

| Capability | Status |
|---|---|
| Real local FASTQ QC, outputs, logs | **Implemented and integration-tested** |
| Local authenticated API and user interface | **Implemented; API-tested** |
| Nextflow subprocess adapter and schema-driven fields | Implemented; **Nextflow runtime not testable here** |
| PostgreSQL / Docker Compose | Configured; not exercised in authoring environment |
| Experiment editor / imports / file selection | Implemented; browser automation blocked in authoring environment |
| Passive local/Tailscale/HTCondor inventory | Implemented; external sources require actual installations |
| Inventory-only signed worker heartbeat | Implemented and API-tested |
| Node CPU/idle policy saving | Implemented; **not OS/scheduler enforced** |
| Real remote worker scheduling | **Not implemented** |
| Public Nextflow Registry catalog/Git cloning | **Implemented; mocked catalog tests, live network not verified** |
| Visual editor drag/drop, connections, save/reload | **Implemented; DOM interaction tested**; DSL2 compilation not implemented |
| Execution stage and published-file inspector | **Implemented; local QC output and trace parsing tested** |
| Full React/TypeScript frontend | **Not implemented** — frontend intentionally dependency-free for offline runnable MVP |
| Multi-user RBAC/audit/production security hardening | **Not implemented** |

Read [Security and limitations](docs/SECURITY.md) before deploying with institutional data. This is not a medically validated or production-hardened system.
