from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
from sqlalchemy.orm import Session
from .config import PROJECT_ROOT, PIPELINE_DIR
from .models import Pipeline

BUILTIN_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "FASTQ quality overview",
    "description": "Compute read count, length, mean Phred quality and GC content for every sample using a real local Python analysis.",
    "properties": {
        "max_reads": {"type": "integer", "title": "Maximum reads per file", "default": 100000, "minimum": 1, "maximum": 5000000, "description": "For demonstration or quick screening; sampling is from the start of each file."},
        "min_length": {"type": "integer", "title": "Warning read length threshold", "default": 20, "minimum": 0, "maximum": 1000},
    },
}


def seed_pipelines(session: Session) -> None:
    entries = [
        ("builtin-fastq-qc", "FASTQ Quality Explorer", "A real, local FASTQ QC workflow with CSV and HTML results. No Nextflow dependency.", "Quality control", "builtin", None, BUILTIN_SCHEMA),
        ("nextflow-fastq-qc", "Nextflow FASTQ QC", "Executable Nextflow DSL2 example. Requires Nextflow/Java and local execution tools.", "Nextflow", "nextflow", str(PROJECT_ROOT / "examples" / "nextflow_fastq_qc"), None),
    ]
    for pid, name, desc, category, engine, path, schema in entries:
        if session.get(Pipeline, pid):
            continue
        if schema is None:
            schema = load_schema(Path(path))
        session.add(Pipeline(id=pid, name=name, description=desc, category=category, engine=engine, source_path=path, schema=schema))
    session.commit()


def load_schema(directory: Path) -> dict:
    file = directory / "nextflow_schema.json"
    if not file.is_file():
        return {"properties": {"input": {"type": "string", "title": "Samplesheet", "format": "file-path", "description": "Path to pipeline samplesheet."}}}
    try:
        result = json.loads(file.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"Invalid nextflow_schema.json: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError("Pipeline schema must be a JSON object")
    # Embed the published samplesheet JSON schema so the launcher can generate
    # precisely the columns demanded by the selected pipeline.
    input_schema = directory / "assets" / "schema_input.json"
    if input_schema.is_file():
        try:
            parsed = json.loads(input_schema.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                result["x-biocompute-input-schema"] = parsed
        except (ValueError, OSError):
            pass
    return result


def sample_columns(schema: dict) -> list[dict]:
    doc = schema.get("x-biocompute-input-schema") or {}
    items = doc.get("items", {})
    if not isinstance(items, dict):
        items = {}
    properties = items.get("properties", doc.get("properties", {}))
    required = set(items.get("required", doc.get("required", [])))
    if not isinstance(properties, dict) or not properties:
        return [{"name": n, "required": n in ("sample", "fastq_1"), "type": "string"} for n in ("sample", "fastq_1", "fastq_2")]
    return [{"name": name, "required": name in required, "type": definition.get("type", "string"),
             "description": definition.get("description", ""), "format": definition.get("format", "")}
            for name, definition in properties.items() if isinstance(definition, dict)]


def flatten_properties(schema: dict) -> list[dict]:
    fields = []
    def walk(properties: dict, group: str = "General", required: list[str] | None = None):
        for name, spec in properties.items():
            if not isinstance(spec, dict):
                continue
            if "properties" in spec and isinstance(spec["properties"], dict):
                walk(spec["properties"], spec.get("title", name), spec.get("required", []))
            else:
                fields.append({"name": name, "group": group, "required": name in (required or []), **spec})
    walk(schema.get("properties", {}), required=schema.get("required", []))
    for group_key in ("definitions", "$defs"):
        for name, definition in schema.get(group_key, {}).items():
            if isinstance(definition, dict):
                walk(definition.get("properties", {}), definition.get("title", name), definition.get("required", []))
    return fields


def validate_params(schema: dict, params: dict) -> dict:
    """Validate common Nextflow JSON-schema properties; preserve advanced params for native pipeline validation."""
    fields = {field["name"]: field for field in flatten_properties(schema)}
    clean = {}
    for name, field in fields.items():
        if name not in params:
            if field.get("required") and field.get("default") is None and name != "input":
                raise ValueError(f"Missing required parameter: {name}")
            if "default" in field:
                clean[name] = field["default"]
            continue
        value = params[name]
        if value is None or value == "":
            if field.get("required") and name != "input":
                raise ValueError(f"Required parameter is empty: {name}")
            continue
        typ = field.get("type")
        if typ == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        elif typ == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
        elif typ == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be true or false")
        elif typ == "string":
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"{name}: value is not in allowed options")
        if isinstance(value, (int, float)):
            if "minimum" in field and value < field["minimum"]:
                raise ValueError(f"{name} is below minimum")
            if "maximum" in field and value > field["maximum"]:
                raise ValueError(f"{name} is above maximum")
        if isinstance(value, str) and "pattern" in field:
            try:
                if not re.search(field["pattern"], value):
                    raise ValueError(f"{name}: invalid format")
            except re.error:
                pass
        clean[name] = value
    # Permit additional Nextflow options: pipeline itself validates them.
    for name, value in params.items():
        if name not in fields:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("Invalid parameter name")
            clean[name] = value
    return clean


def pipeline_info(p: Pipeline) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description,
            "category": p.category, "engine": p.engine, "source_path": p.source_path,
            "version": p.version, "schema": p.schema or {}, "fields": flatten_properties(p.schema or {}),
            "sample_columns": sample_columns(p.schema or {})}
