from pydantic import BaseModel, Field
from typing import Any


class SampleInput(BaseModel):
    sample: str = Field(min_length=1, max_length=180)
    condition: str = Field(default="Control", max_length=160)
    replicate: int = Field(default=1, ge=1)
    file1: str = ""
    file2: str = ""
    batch: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    assay: str = "Generic"
    samples: list[SampleInput] = Field(default_factory=list)


class RunCreate(BaseModel):
    pipeline_id: str
    experiment_id: int | None = None
    name: str = Field(default="Untitled analysis", min_length=1, max_length=160)
    params: dict[str, Any] = Field(default_factory=dict)
    samples: list[SampleInput] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)


class PipelineImport(BaseModel):
    path: str
    name: str = ""
    category: str = "Imported"


class PresetCreate(BaseModel):
    pipeline_id: str
    name: str = Field(min_length=1, max_length=160)
    parameters: dict[str, Any]


class NodePatch(BaseModel):
    authorized: bool | None = None
    enabled: bool | None = None
    cpu_limit: int | None = Field(default=None, ge=0)
    ram_limit: int | None = Field(default=None, ge=0)
    idle_only: bool | None = None


class NodeManual(BaseModel):
    hostname: str = Field(min_length=1, max_length=180)
    address: str = Field(default="", max_length=240)


class GraphCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
