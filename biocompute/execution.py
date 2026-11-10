"""Read-only provenance view of local pipeline execution.

Published files are confirmed artifacts under results/; files in Nextflow task
workspaces are *observed* workspace files and may include copied input files.
"""
from __future__ import annotations

import csv
import os
import re
from pathlib import Path
from typing import Any

MAX_FILES = 500
MAX_TASKS = 250
TRACE_HASH = re.compile(r'^[a-fA-F0-9]{2}/[a-fA-F0-9]{2,64}$')


def files_under(root: Path, max_items: int = MAX_FILES, *, published: bool = False) -> list[dict[str, Any]]:
    """Collect regular files in a bounded, non-symlink-following traversal."""
    if not root.is_dir():
        return []
    found = []
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = sorted(d for d in folders if not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink():
                # Nextflow often publishes symlinks to files within this run's work/ tree.
                # Exclude links to arbitrary data, particularly symlinked input files.
                target = path.resolve()
                work_root = (root.parent / 'work').resolve()
                if not published or work_root not in target.parents or not target.is_file():
                    continue
            elif not path.is_file():
                continue
            try:
                rel = path.relative_to(root).as_posix()
                size = path.stat().st_size
            except (ValueError, OSError):
                continue
            found.append({'path': rel, 'name': name, 'bytes': size, 'linked_workfile': path.is_symlink()})
            if len(found) >= max_items:
                return found
    return found


def nextflow_tasks(workdir: Path) -> list[dict[str, Any]]:
    trace_path = workdir / 'results' / 'trace.txt'
    if not trace_path.is_file():
        return []
    tasks = []
    try:
        with trace_path.open(newline='', encoding='utf-8', errors='replace') as handle:
            reader = csv.DictReader(handle, delimiter='\t')
            for record in reader:
                if len(tasks) >= MAX_TASKS:
                    break
                name = record.get('name') or record.get('process') or 'Nextflow task'
                status = record.get('status') or 'UNKNOWN'
                hash_value = (record.get('hash') or '').strip()
                files = []
                if TRACE_HASH.fullmatch(hash_value):
                    prefix, suffix = hash_value.split('/')
                    base = workdir / 'work' / prefix
                    if base.is_dir():
                        candidates = sorted(p for p in base.glob(suffix + '*') if p.is_dir() and not p.is_symlink())[:1]
                        if candidates:
                            files = [f for f in files_under(candidates[0], 30)
                                     if not f['name'].startswith('.command') and not f['name'].startswith('.exitcode')]
                tasks.append({'name': name, 'status': status, 'hash': hash_value,
                              'duration': record.get('realtime') or record.get('duration') or '',
                              'workspace_files': files})
    except (OSError, csv.Error, UnicodeError):
        return []
    return tasks


def execution_snapshot(workdir: Path, engine: str, status: str) -> dict:
    """No network calls or executing user-controlled code; only read scoped run files."""
    published = files_under(workdir / 'results', published=True)
    tasks = nextflow_tasks(workdir) if engine == 'nextflow' else []
    if engine == 'builtin':
        tasks = [{'name': 'FASTQ Quality Explorer', 'status': status.upper(), 'hash': '',
                  'duration': '', 'workspace_files': []}]
    return {
        'engine': engine, 'status': status,
        'tasks': tasks,
        'published': published,
        'published_count': len(published),
        'task_file_note': ('Workspace files are observed in Nextflow task directories and may include '
                           'copied inputs or intermediate files. Only files under results/ are '
                           'confirmed published outputs.'),
        'trace_available': (workdir / 'results' / 'trace.txt').is_file(),
        'partial': status in ('queued', 'running'),
        'truncated': len(published) >= MAX_FILES or len(tasks) >= MAX_TASKS,
    }


def safe_output_path(workdir: Path, rel: str) -> Path:
    """Only download run-owned regular results or links to run-owned work files."""
    root = (workdir / 'results').resolve()
    work_root = (workdir / 'work').resolve()
    if not root.is_dir() or not rel or rel.startswith('/') or '\\' in rel:
        raise ValueError('Invalid result path')
    parts = Path(rel).parts
    if any(part in ('.', '..') for part in parts):
        raise ValueError('Invalid traversal')
    raw = root.joinpath(*parts)
    if any(root.joinpath(*parts[:i]).is_symlink() for i in range(1, len(parts))):
        raise ValueError('Symlinked subdirectories are not allowed')
    path = raw.resolve(strict=True)
    if not path.is_file():
        raise ValueError('Not a file')
    if path != raw and work_root not in path.parents:
        raise ValueError('Symlink artifact does not belong to this run')
    if root not in path.parents and work_root not in path.parents:
        raise ValueError('Output is outside the run directory')
    return path
