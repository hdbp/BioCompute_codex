from __future__ import annotations

import csv
import hmac
import json
import os
import re
import shutil
import subprocess
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import psutil
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from . import __version__
from .config import PROJECT_ROOT, STATE_DIR, WORK_DIR, PIPELINE_DIR, admin_token, agent_token, allowed_roots, bootstrap, safe_path
from .db import SessionLocal, initialize
from .discovery import discover, node_info
from .models import Experiment, Graph, Node, Pipeline, Preset, Run
from .pipelines import pipeline_info, seed_pipelines, validate_params, load_schema, sample_columns
from .runner import runner, run_info
from .execution import execution_snapshot, safe_output_path
from .registry import list_registry_pipelines, IDENT
from .schemas import ExperimentCreate, GraphCreate, NodeManual, NodePatch, PipelineImport, PresetCreate, RunCreate


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    initialize()
    with SessionLocal() as db:
        seed_pipelines(db)
        discover(db)
    runner.recover()
    yield


app = FastAPI(title="BioCompute", description="Bioinformatics pipeline control and compute inventory API", version=__version__, lifespan=lifespan)


def db_session():
    with SessionLocal() as db:
        yield db


def require_auth(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, "Authentication required")
    if not hmac.compare_digest(authorization.removeprefix('Bearer ').strip(), admin_token()):
        raise HTTPException(401, "Invalid access token")


api_auth = [Depends(require_auth)]


@app.get('/api/health')
def health():
    return {'status': 'ok', 'version': __version__}


class LoginRequest(BaseModel):
    token: str


@app.post('/api/auth/login')
def login(data: LoginRequest):
    if not hmac.compare_digest(data.token, admin_token()):
        raise HTTPException(401, 'Invalid access token')
    return {'ok': True}


@app.get('/api/system', dependencies=api_auth)
def system_info():
    return {
        'version': __version__, 'hostname': socket.gethostname(),
        'data_roots': [str(x) for x in allowed_roots() if x.exists()],
        'nextflow': bool(shutil.which('nextflow')),
        'htcondor': bool(shutil.which('condor_status')),
        'tailscale': bool(shutil.which('tailscale')),
        'execution_scope': 'local-controller-only',
        'discovery_scope': 'local host, passive LAN neighbor table, Tailscale, HTCondor, signed worker heartbeats',
        'note': 'Remote node execution and visual DSL2 code generation are not part of this first milestone.',
    }


@app.get('/api/dashboard', dependencies=api_auth)
def dashboard(db: Session = Depends(db_session)):
    nodes = db.query(Node).all()
    runs = db.query(Run).order_by(desc(Run.created_at)).limit(8).all()
    statuses = dict(db.query(Run.status, func.count(Run.id)).group_by(Run.status).all())
    return {
        'nodes': {'discovered': len(nodes), 'authorized': sum(n.authorized for n in nodes),
                  'active': sum(n.id == 'local-controller' and n.enabled for n in nodes),
                  'total_known_cpu': sum(n.cpu_total for n in nodes)},
        'jobs': statuses, 'recent_runs': [run_info(x) for x in runs],
        'pipelines': db.query(Pipeline).count(), 'experiments': db.query(Experiment).count(),
        'controller': node_info(next(n for n in nodes if n.id == 'local-controller')) if nodes else None,
    }


class WorkerHeartbeat(BaseModel):
    machine_id: str
    hostname: str
    os: str
    arch: str
    cpu_total: int
    ram_total: int
    cpu_percent: float = 0
    ram_used: int = 0
    address: str = ""


@app.post('/api/agents/heartbeat')
def worker_heartbeat(payload: WorkerHeartbeat, x_agent_token: str | None = Header(default=None), db: Session = Depends(db_session)):
    """Explicitly configured signed worker announces inventory; cannot execute jobs."""
    if not x_agent_token or not hmac.compare_digest(x_agent_token, agent_token()):
        raise HTTPException(401, 'Invalid worker discovery token')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{6,96}', payload.machine_id):
        raise HTTPException(422, 'Invalid machine identity')
    if not (1 <= payload.cpu_total <= 4096) or payload.ram_total < 0:
        raise HTTPException(422, 'Invalid resource inventory')
    node_id = 'agent-' + payload.machine_id
    node = db.get(Node, node_id)
    details = {'cpu_percent': payload.cpu_percent, 'ram_used': payload.ram_used,
               'note': 'Agent heartbeat received; execution not enabled'}
    if node is None:
        node = Node(id=node_id, hostname=payload.hostname[:180], os=payload.os[:80],
                    arch=payload.arch[:80], cpu_total=payload.cpu_total, ram_total=payload.ram_total,
                    source='biocompute-agent', address=payload.address[:240], status='discovered',
                    authorized=False, enabled=False, cpu_limit=0, ram_limit=0,
                    last_seen=datetime.now(timezone.utc), details=details)
        db.add(node)
    else:
        node.hostname = payload.hostname[:180]
        node.os = payload.os[:80]
        node.arch = payload.arch[:80]
        node.cpu_total = payload.cpu_total
        node.ram_total = payload.ram_total
        node.address = payload.address[:240]
        node.last_seen = datetime.now(timezone.utc)
        node.details = details
    db.commit()
    return {'received': True, 'node_id': node_id, 'execution_enabled': False}


@app.get('/api/nodes', dependencies=api_auth)
def nodes(db: Session = Depends(db_session)):
    return [node_info(n) for n in db.query(Node).order_by(Node.source, Node.hostname).all()]


@app.post('/api/nodes/discover', dependencies=api_auth)
def refresh_nodes(db: Session = Depends(db_session)):
    discovered = discover(db)
    return {'scanned_sources': discovered, 'nodes': nodes(db)}


@app.post('/api/nodes/manual', dependencies=api_auth)
def add_manual_node(data: NodeManual, db: Session = Depends(db_session)):
    # A manual record is only a candidate, never a working/enrolled host.
    node_id = 'manual-' + uuid.uuid4().hex[:12]
    n = Node(id=node_id, hostname=data.hostname, address=data.address, os='unknown', arch='unknown',
             cpu_total=0, ram_total=0, source='manual', status='discovered', authorized=False,
             enabled=False, cpu_limit=0, ram_limit=0, details={'note': 'Manual inventory entry; agent enrollment not yet implemented'})
    db.add(n)
    db.commit()
    return node_info(n)


@app.patch('/api/nodes/{node_id}', dependencies=api_auth)
def patch_node(node_id: str, data: NodePatch, db: Session = Depends(db_session)):
    n = db.get(Node, node_id)
    if not n:
        raise HTTPException(404, 'Node not found')
    updates = data.model_dump(exclude_unset=True)
    if n.id != 'local-controller' and updates.get('enabled'):
        raise HTTPException(409, 'Remote execution requires a verified worker agent and scheduler; recruitment is not implemented yet')
    if n.id == 'local-controller' and updates.get('authorized') is False:
        raise HTTPException(409, 'Cannot unauthorize the controller')
    if n.id == 'local-controller' and updates.get('enabled') is False:
        raise HTTPException(409, 'Controller remains enabled; stop the service to disable it')
    if 'cpu_limit' in updates and n.cpu_total and updates['cpu_limit'] > n.cpu_total:
        raise HTTPException(422, 'CPU allocation exceeds detected logical cores')
    if 'ram_limit' in updates and n.ram_total and updates['ram_limit'] > n.ram_total:
        raise HTTPException(422, 'RAM allocation exceeds detected capacity')
    for key, val in updates.items():
        setattr(n, key, val)
    db.commit()
    return node_info(n)


@app.get('/api/pipelines', dependencies=api_auth)
def pipelines(db: Session = Depends(db_session)):
    return [pipeline_info(p) for p in db.query(Pipeline).order_by(Pipeline.name).all()]


@app.get('/api/registry/pipelines', dependencies=api_auth)
def registry_pipelines(search: str = Query(default='', max_length=120), refresh: bool = False):
    """Read all public pages of the Nextflow Registry; never executes fetched code."""
    try:
        return list_registry_pipelines(force=refresh, search=search)
    except Exception as exc:
        raise HTTPException(503, f'Unable to fetch Nextflow Registry: {exc}') from exc


class RegistryImport(BaseModel):
    pipeline_id: str
    confirm_untrusted_code: bool = False


@app.post('/api/registry/import', dependencies=api_auth)
def import_registry_pipeline(payload: RegistryImport, db: Session = Depends(db_session)):
    if not payload.confirm_untrusted_code:
        raise HTTPException(422, 'Confirm that importing a third-party workflow includes potentially executable code')
    if not IDENT.fullmatch(payload.pipeline_id):
        raise HTTPException(422, 'Invalid pipeline identifier')
    try:
        listing = list_registry_pipelines()
    except Exception as exc:
        raise HTTPException(503, f'Cannot verify pipeline in registry: {exc}') from exc
    item = next((x for x in listing['pipelines'] if x['id'] == payload.pipeline_id), None)
    if item is None:
        raise HTTPException(404, 'Pipeline not published in Nextflow Registry')
    if not shutil.which('git'):
        raise HTTPException(409, 'Git is required for importing registry pipelines')
    target = PIPELINE_DIR / ('registry-' + payload.pipeline_id.replace('/', '-'))
    if target.exists():
        raise HTTPException(409, 'Pipeline already downloaded; remove or update it manually before reimporting')
    target.parent.mkdir(parents=True, exist_ok=True)
    url = 'https://github.com/' + payload.pipeline_id + '.git'
    # Clone without invoking a shell, and do not execute any code from the repository.
    try:
        subprocess.run(['git', 'clone', '--depth', '1', '--single-branch', '--', url, str(target)],
                       check=True, capture_output=True, text=True, timeout=180,
                       env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        if not (target / 'main.nf').is_file():
            raise ValueError('Repository does not contain main.nf at root')
        revision = subprocess.run(['git','-C',str(target),'rev-parse','HEAD'],
                                  check=True,capture_output=True,text=True,timeout=10).stdout.strip()
        schema = load_schema(target)
        row = Pipeline(id='registry-' + uuid.uuid4().hex[:10],
                       name=payload.pipeline_id, description=f"{item['description']} (commit {revision[:12]})",
                       category='Registry', engine='nextflow', source_path=str(target), schema=schema, version=revision[:12])
        db.add(row)
        db.commit()
        return {**pipeline_info(row), 'revision': revision, 'warning': 'Review repository code before running it.'}
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        if target.exists():
            shutil.rmtree(target)
        raise HTTPException(422, f'Pipeline import failed: {str(exc)[:350]}') from exc


@app.get('/api/pipelines/{pipeline_id}', dependencies=api_auth)
def pipeline(pipeline_id: str, db: Session = Depends(db_session)):
    item = db.get(Pipeline, pipeline_id)
    if not item:
        raise HTTPException(404, 'Pipeline not found')
    return pipeline_info(item)


@app.post('/api/pipelines/import', dependencies=api_auth)
def import_pipeline(data: PipelineImport, db: Session = Depends(db_session)):
    roots = [PIPELINE_DIR, PROJECT_ROOT / 'examples']
    for p in os.environ.get('BIOCOMPUTE_PIPELINE_ROOTS', '').split(os.pathsep):
        if p.strip():
            roots.append(Path(p).expanduser().resolve())
    path = Path(data.path).expanduser().resolve()
    if not any(path == root or root in path.parents for root in roots):
        raise HTTPException(403, 'Pipeline path must be inside an approved pipeline root')
    if not path.is_dir() or not (path / 'main.nf').is_file():
        raise HTTPException(422, 'Expected a Nextflow directory containing main.nf')
    try:
        schema = load_schema(path)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    item = Pipeline(id='import-' + uuid.uuid4().hex[:10], name=data.name or path.name,
                    description=f'Imported from {path}', category=data.category,
                    engine='nextflow', source_path=str(path), schema=schema)
    db.add(item)
    db.commit()
    return pipeline_info(item)


@app.get('/api/presets', dependencies=api_auth)
def presets(pipeline_id: str | None = None, db: Session = Depends(db_session)):
    q = db.query(Preset)
    if pipeline_id:
        q = q.filter_by(pipeline_id=pipeline_id)
    return [{'id': p.id, 'pipeline_id': p.pipeline_id, 'name': p.name, 'parameters': p.parameters} for p in q.all()]


@app.post('/api/presets', dependencies=api_auth)
def create_preset(payload: PresetCreate, db: Session = Depends(db_session)):
    p = db.get(Pipeline, payload.pipeline_id)
    if not p:
        raise HTTPException(404, 'Pipeline not found')
    try:
        clean = validate_params(p.schema or {}, payload.parameters)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    new = Preset(name=payload.name, pipeline_id=p.id, parameters=clean)
    db.add(new)
    db.commit()
    return {'id': new.id, 'name': new.name}


@app.get('/api/files', dependencies=api_auth)
def files(path: str | None = None):
    if not path:
        return {'roots': [{'name': p.name or str(p), 'path': str(p)} for p in allowed_roots() if p.is_dir()], 'path': None, 'entries': []}
    try:
        location = safe_path(path)
    except (ValueError, OSError) as exc:
        raise HTTPException(403, str(exc)) from exc
    if not location.is_dir():
        raise HTTPException(422, 'Not a directory')
    entries = []
    try:
        children = sorted(location.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:500]
        for child in children:
            try:
                real = safe_path(child)
                info = child.stat()
                entries.append({'name': child.name, 'path': str(real), 'directory': child.is_dir(),
                                'bytes': info.st_size if child.is_file() else 0})
            except (OSError, ValueError):
                continue
    except PermissionError:
        raise HTTPException(403, 'Unable to read this directory')
    parent = location.parent
    can_up = any(parent == root or root in parent.parents for root in allowed_roots())
    return {'path': str(location), 'parent': str(parent) if can_up else None, 'entries': entries, 'roots': []}


def validate_samples(samples: list[dict]) -> dict:
    problems, warnings = [], []
    seen = set()
    if not samples:
        problems.append('Add at least one sample.')
    for i, s in enumerate(samples, 1):
        name = s.get('sample', '').strip()
        if not name:
            problems.append(f'Row {i}: sample name is required')
        if name and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,179}', name):
            problems.append(f'Row {i}: sample ID may use only letters, digits, underscore, dot or dash')
        if name in seen:
            problems.append(f'Row {i}: duplicate sample name {name}')
        seen.add(name)
        if not s.get('file1'):
            problems.append(f'Row {i}: R1/input file is required')
        for label in ('file1', 'file2'):
            if s.get(label):
                try:
                    p = safe_path(s[label])
                    if not p.is_file():
                        problems.append(f'Row {i}: {label} is not a file')
                    elif p.suffix.lower() not in ('.fastq', '.fq', '.gz', '.csv', '.tsv', '.mzml', '.raw'):
                        warnings.append(f'Row {i}: unrecognized file extension: {p.name}')
                except (ValueError, OSError):
                    problems.append(f'Row {i}: {label} is outside approved data roots or missing')
    return {'valid': not problems, 'errors': problems, 'warnings': warnings}


@app.post('/api/experiments/validate', dependencies=api_auth)
def validate_experiment(payload: ExperimentCreate):
    return validate_samples([s.model_dump() for s in payload.samples])


@app.get('/api/experiments', dependencies=api_auth)
def experiments(db: Session = Depends(db_session)):
    return [{'id': x.id, 'name': x.name, 'assay': x.assay, 'sample_count': len(x.samples or []),
             'samples': x.samples, 'updated_at': x.updated_at.isoformat() if x.updated_at else None}
            for x in db.query(Experiment).order_by(desc(Experiment.updated_at)).all()]


@app.post('/api/experiments', dependencies=api_auth)
def save_experiment(payload: ExperimentCreate, db: Session = Depends(db_session)):
    row = Experiment(name=payload.name, assay=payload.assay, samples=[s.model_dump() for s in payload.samples])
    db.add(row)
    db.commit()
    return {'id': row.id, 'name': row.name, 'sample_count': len(row.samples)}


@app.put('/api/experiments/{experiment_id}', dependencies=api_auth)
def update_experiment(experiment_id: int, payload: ExperimentCreate, db: Session = Depends(db_session)):
    row = db.get(Experiment, experiment_id)
    if not row:
        raise HTTPException(404, 'Experiment not found')
    row.name, row.assay, row.samples = payload.name, payload.assay, [s.model_dump() for s in payload.samples]
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {'id': row.id, 'name': row.name, 'sample_count': len(row.samples)}


@app.get('/api/runs', dependencies=api_auth)
def runs(db: Session = Depends(db_session)):
    return [run_info(x) for x in db.query(Run).order_by(desc(Run.created_at)).limit(100).all()]


@app.post('/api/runs', dependencies=api_auth)
def create_run(payload: RunCreate, db: Session = Depends(db_session)):
    pipeline = db.get(Pipeline, payload.pipeline_id)
    if not pipeline:
        raise HTTPException(404, 'Pipeline not found')
    samples = [s.model_dump() for s in payload.samples]
    if payload.experiment_id:
        experiment = db.get(Experiment, payload.experiment_id)
        if not experiment:
            raise HTTPException(404, 'Experiment not found')
        if not samples:
            samples = experiment.samples
    validation = validate_samples(samples)
    if not validation['valid']:
        raise HTTPException(422, {'sample_validation': validation})
    if pipeline.engine == 'builtin':
        for s in samples:
            for key in ('file1', 'file2'):
                if s.get(key) and not s[key].lower().endswith(('.fastq','.fq','.fastq.gz','.fq.gz')):
                    raise HTTPException(422, 'Built-in QC expects FASTQ or FASTQ.gz input files')
    if payload.node_ids and payload.node_ids != ['local-controller']:
        raise HTTPException(409, 'This milestone supports local controller execution only; remote scheduling is not yet implemented')
    if pipeline.engine == 'nextflow':
        aliases = {'sample':'sample', 'sample_id':'sample', 'fastq_1':'file1', 'fastq_2':'file2',
                   'r1':'file1', 'r2':'file2', 'condition':'condition', 'replicate':'replicate', 'batch':'batch'}
        for col in sample_columns(pipeline.schema or {}):
            if not col.get('required'):
                continue
            key = col['name']
            for s in samples:
                val = s.get('metadata', {}).get(key, s.get(aliases.get(key, key)))
                if val is None or val == '':
                    raise HTTPException(422, f"Sample {s['sample']} is missing required pipeline-specific field: {key}")
    try:
        params = validate_params(pipeline.schema or {}, payload.params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if pipeline.engine == 'nextflow':
        if not shutil.which('nextflow'):
            raise HTTPException(409, 'Nextflow is not installed on the controller. Use the built-in QC pipeline or install Nextflow.')
        # Override managed parameters so user-supplied paths cannot redirect output or bypass approved input validation.
        params.pop('input', None)
        params.pop('outdir', None)
    identifier = str(uuid.uuid4())
    run_dir = (WORK_DIR / identifier).resolve()
    row = Run(id=identifier, pipeline_id=pipeline.id, experiment_id=payload.experiment_id,
              name=payload.name, status='queued', params=params, samples=samples,
              node_ids=['local-controller'], workdir=str(run_dir))
    db.add(row)
    db.commit()
    runner.start(identifier)
    return run_info(row)


@app.get('/api/runs/{run_id}', dependencies=api_auth)
def get_run(run_id: str, db: Session = Depends(db_session)):
    row = db.get(Run, run_id)
    if not row:
        raise HTTPException(404, 'Run not found')
    return run_info(row)


@app.post('/api/runs/{run_id}/cancel', dependencies=api_auth)
def cancel_run(run_id: str, db: Session = Depends(db_session)):
    row = db.get(Run, run_id)
    if not row:
        raise HTTPException(404, 'Run not found')
    if row.status not in ('queued', 'running'):
        raise HTTPException(409, 'Only queued or running jobs can be cancelled')
    runner.cancel(run_id)
    return {'accepted': True, 'status': 'cancellation-requested'}


@app.get('/api/runs/{run_id}/logs', dependencies=api_auth)
def logs(run_id: str, offset: int = Query(default=0, ge=0), db: Session = Depends(db_session)):
    row = db.get(Run, run_id)
    if not row:
        raise HTTPException(404, 'Run not found')
    path = Path(row.workdir) / 'run.log'
    if not path.exists():
        return {'content': '', 'next_offset': 0, 'status': row.status}
    with path.open('rb') as file:
        file.seek(offset)
        chunk = file.read(256 * 1024)
        next_offset = file.tell()
    return {'content': chunk.decode('utf-8', errors='replace'), 'next_offset': next_offset, 'status': row.status}


@app.get('/api/runs/{run_id}/execution', dependencies=api_auth)
def run_execution(run_id: str, db: Session = Depends(db_session)):
    row = db.get(Run, run_id)
    if row is None:
        raise HTTPException(404, 'Run not found')
    pipe = db.get(Pipeline, row.pipeline_id)
    return execution_snapshot(Path(row.workdir), pipe.engine if pipe else 'unknown', row.status)


@app.get('/api/runs/{run_id}/artifacts', dependencies=api_auth)
def artifacts(run_id: str, db: Session = Depends(db_session)):
    row = db.get(Run, run_id)
    if not row:
        raise HTTPException(404, 'Run not found')
    return execution_snapshot(Path(row.workdir), 'unknown', row.status)['published']


@app.get('/api/runs/{run_id}/artifacts/{name:path}', dependencies=api_auth)
def download_artifact(run_id: str, name: str, db: Session = Depends(db_session)):
    row = db.get(Run, run_id)
    if not row:
        raise HTTPException(404, 'Run not found')
    try:
        path = safe_output_path(Path(row.workdir), name)
    except (ValueError, OSError):
        raise HTTPException(404, 'Artifact not found')
    return FileResponse(path, filename=path.name, media_type='application/octet-stream')


@app.get('/api/studio/graphs', dependencies=api_auth)
def graphs(db: Session = Depends(db_session)):
    return [{'id': g.id, 'name': g.name, 'nodes': g.nodes, 'edges': g.edges} for g in db.query(Graph).order_by(desc(Graph.updated_at)).all()]


@app.post('/api/studio/graphs', dependencies=api_auth)
def save_graph(payload: GraphCreate, db: Session = Depends(db_session)):
    # Saved graph is a draft. Arbitrary workflow graph -> DSL2 export is not implemented.
    if len(payload.nodes) > 100 or len(payload.edges) > 200:
        raise HTTPException(422, 'Graph is too large')
    g = Graph(name=payload.name, nodes=payload.nodes, edges=payload.edges)
    db.add(g)
    db.commit()
    return {'id': g.id, 'name': g.name}


FRONTEND = PROJECT_ROOT / 'frontend'
app.mount('/assets', StaticFiles(directory=FRONTEND), name='frontend-assets')


@app.get('/')
def index():
    return FileResponse(FRONTEND / 'index.html')


def main():
    import uvicorn
    uvicorn.run('biocompute.app:app', host=os.environ.get('BIOCOMPUTE_HOST', '127.0.0.1'),
                port=int(os.environ.get('BIOCOMPUTE_PORT', '8000')), reload=False)


if __name__ == '__main__':
    main()
