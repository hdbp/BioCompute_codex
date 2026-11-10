"""Persistent local runner: genuine process execution, streamed logs, cancellation."""
from __future__ import annotations
import csv
import json
import os
import shutil
import signal
import subprocess
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .analysis import run_fastq_qc
from .config import WORK_DIR
from .db import SessionLocal
from .models import Pipeline, Run
from .pipelines import sample_columns


def now():
    return datetime.now(timezone.utc)


class RunService:
    def __init__(self):
        self.processes: dict[str, subprocess.Popen] = {}
        self.cancellations: set[str] = set()
        self.lock = threading.RLock()

    def recover(self):
        # A future restart does not silently claim old jobs are still running.
        with SessionLocal() as db:
            for row in db.query(Run).filter(Run.status.in_(["queued", "running"])).all():
                row.status = "interrupted"
                row.error = "Controller restarted. Review the run and submit it again; Nextflow work dirs are preserved."
                row.finished_at = now()
            db.commit()

    def start(self, run_id: str):
        threading.Thread(target=self._execute, args=(run_id,), daemon=True, name=f"biocompute-{run_id[:8]}").start()

    def cancel(self, run_id: str):
        with self.lock:
            self.cancellations.add(run_id)
            proc = self.processes.get(run_id)
            if proc and proc.poll() is None:
                try:
                    if os.name == 'posix':
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    else:
                        proc.terminate()
                except (OSError, ProcessLookupError):
                    pass

    def _execute(self, run_id: str):
        with SessionLocal() as db:
            row = db.get(Run, run_id)
            if not row:
                return
            row.status = 'running'
            row.started_at = now()
            db.commit()
            pipe = db.get(Pipeline, row.pipeline_id)
            if not pipe:
                row.status = 'failed'
                row.error = 'Pipeline was deleted'
                db.commit()
                return
            engine, source = pipe.engine, pipe.source_path
            columns = sample_columns(pipe.schema or {})
            parameters, samples, workdir = dict(row.params), list(row.samples), Path(row.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        results = workdir / 'results'
        results.mkdir(exist_ok=True)
        log_path = workdir / 'run.log'
        exit_code = 0
        error = None
        with log_path.open('a', encoding='utf-8', buffering=1) as log:
            def emit(line):
                log.write(str(line).rstrip() + '\n')
            try:
                emit('BioCompute run ' + run_id)
                emit('Engine: ' + engine)
                if run_id in self.cancellations:
                    raise InterruptedError('Run cancelled before launch')
                if engine == 'builtin':
                    run_fastq_qc(samples, parameters, results, emit)
                elif engine == 'nextflow':
                    if not shutil.which('nextflow'):
                        raise RuntimeError('Nextflow executable not available. Install Nextflow on the execution host; there is no simulated fallback.')
                    if not source or not Path(source).is_dir():
                        raise RuntimeError('Nextflow pipeline directory not found')
                    sample_path = workdir / 'samplesheet.csv'
                    self._write_samplesheet(sample_path, samples, columns)
                    parameters = {**parameters, 'input': str(sample_path), 'outdir': str(results)}
                    parameters_path = workdir / 'params.json'
                    parameters_path.write_text(json.dumps(parameters, indent=2), encoding='utf-8')
                    cmd = [shutil.which('nextflow'), 'run', str(Path(source).resolve()),
                           '-params-file', str(parameters_path), '-work-dir', str(workdir / 'work'),
                           '-with-trace', str(results / 'trace.txt'),
                           '-with-report', str(results / 'nextflow-report.html'),
                           '-with-timeline', str(results / 'nextflow-timeline.html'),
                           '-with-dag', str(results / 'nextflow-dag.html')]
                    emit('Launching: nextflow run [pipeline] -params-file params.json -work-dir [work] -with-trace [trace]')
                    self._run_process(run_id, cmd, workdir, emit)
                else:
                    raise RuntimeError(f'Unsupported execution engine: {engine}')
            except InterruptedError as exc:
                error = str(exc)
                exit_code = 130
                emit('Cancelled: ' + error)
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
                exit_code = 1
                emit('ERROR: ' + error)
        with SessionLocal() as db:
            row = db.get(Run, run_id)
            if row:
                row.status = 'cancelled' if run_id in self.cancellations or exit_code == 130 else 'failed' if exit_code else 'completed'
                row.error = error
                row.exit_code = exit_code
                row.finished_at = now()
                db.commit()
        with self.lock:
            self.processes.pop(run_id, None)
            self.cancellations.discard(run_id)

    @staticmethod
    def _write_samplesheet(path: Path, samples: list, columns: list[dict]):
        names = [col['name'] for col in columns]
        aliases = {'sample':'sample', 'sample_id':'sample',
                   'fastq_1':'file1', 'fastq_2':'file2', 'r1':'file1', 'r2':'file2',
                   'condition':'condition', 'replicate':'replicate', 'batch':'batch'}
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=names)
            writer.writeheader()
            for row in samples:
                record={}
                for column in columns:
                    name=column['name']
                    value=row.get('metadata', {}).get(name, row.get(aliases.get(name, name), ''))
                    if column.get('required') and (value is None or value == ''):
                        raise ValueError(f"Samplesheet field '{name}' is required for sample {row.get('sample')}")
                    record[name]=value or ''
                writer.writerow(record)

    def _run_process(self, run_id: str, cmd: list[str], cwd: Path, emit):
        env = os.environ.copy()
        env['NXF_HOME'] = str(WORK_DIR.parent / 'nextflow-home')
        with subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors='replace',
                              bufsize=1, start_new_session=(os.name == 'posix')) as proc:
            with self.lock:
                self.processes[run_id] = proc
            assert proc.stdout is not None
            for line in proc.stdout:
                emit(line)
            exit_code = proc.wait()
            if run_id in self.cancellations:
                raise InterruptedError('User cancelled the run')
            if exit_code != 0:
                raise RuntimeError(f'Nextflow exited with code {exit_code}. Review run.log.')


runner = RunService()


def run_info(row: Run):
    return {'id': row.id, 'pipeline_id': row.pipeline_id, 'experiment_id': row.experiment_id,
            'name': row.name, 'status': row.status, 'params': row.params, 'samples': row.samples,
            'node_ids': row.node_ids, 'created_at': row.created_at.isoformat() if row.created_at else None,
            'started_at': row.started_at.isoformat() if row.started_at else None,
            'finished_at': row.finished_at.isoformat() if row.finished_at else None,
            'exit_code': row.exit_code, 'error': row.error,
            'result_count': len(list((Path(row.workdir)/'results').iterdir())) if (Path(row.workdir)/'results').exists() else 0}
