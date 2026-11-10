from __future__ import annotations

import os
import secrets
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("BIOCOMPUTE_STATE_DIR", PROJECT_ROOT / "state")).expanduser().resolve()
DATA_DIR = Path(os.environ.get("BIOCOMPUTE_DATA_DIR", PROJECT_ROOT / "examples" / "data")).expanduser().resolve()
WORK_DIR = STATE_DIR / "runs"
PIPELINE_DIR = STATE_DIR / "pipelines"
DB_URL = os.environ.get("BIOCOMPUTE_DATABASE_URL", f"sqlite:///{STATE_DIR / 'biocompute.sqlite3'}")


def bootstrap() -> None:
    for folder in (STATE_DIR, DATA_DIR, WORK_DIR, PIPELINE_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    for name in ("admin-token.txt", "agent-token.txt"):
        token_file = STATE_DIR / name
        if not token_file.exists():
            token_file.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
            token_file.chmod(0o600)


def admin_token() -> str:
    provided = os.environ.get("BIOCOMPUTE_ADMIN_TOKEN")
    if provided:
        return provided
    return (STATE_DIR / "admin-token.txt").read_text(encoding="utf-8").strip()


def allowed_roots() -> list[Path]:
    roots = [DATA_DIR]
    for path in os.environ.get("BIOCOMPUTE_DATA_ROOTS", "").split(os.pathsep):
        if path.strip():
            roots.append(Path(path).expanduser().resolve())
    return roots


def safe_path(requested: str | Path, *, allow_nonexistent: bool = False) -> Path:
    value = Path(requested).expanduser()
    resolved = value.resolve(strict=not allow_nonexistent)
    if not any(resolved == root or root in resolved.parents for root in allowed_roots()):
        raise ValueError("Path is outside the approved data roots")
    return resolved


def agent_token() -> str:
    provided = os.environ.get("BIOCOMPUTE_AGENT_TOKEN")
    if provided:
        return provided
    return (STATE_DIR / "agent-token.txt").read_text(encoding="utf-8").strip()
