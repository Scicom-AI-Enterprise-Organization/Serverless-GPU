"""Pydantic result models returned by dawn operations."""

from typing import Optional
from pydantic import BaseModel, Field


class CheckpointResult(BaseModel):
    """Result of a successful Checkpoint.dump() call."""

    path: str = Field(description="Directory containing the checkpoint images")
    size_bytes: int = Field(description="Total size of the checkpoint on disk")
    file_count: int = Field(description="Number of files written")
    duration_ms: int = Field(description="Wall-clock time of the dump operation")
    pid: int = Field(description="PID of the process that was dumped")
    gpu_state_before: str = Field(description="cuda-checkpoint state before dump")
    gpu_state_after: str = Field(description="cuda-checkpoint state after dump (process killed)")
    metadata_dir: Optional[str] = Field(
        default=None,
        description="Where metadata was written when --page-server is used",
    )
    pages_dir: Optional[str] = Field(
        default=None,
        description="Where pages were streamed when --page-server is used",
    )


class RestoreResult(BaseModel):
    """Result of a successful Checkpoint.restore() call."""

    duration_ms: int = Field(description="Wall-clock time of the restore operation")
    pid: int = Field(description="PID of the restored process")
    gpu_state: str = Field(description="cuda-checkpoint state after restore")
    vram_mb: int = Field(description="VRAM allocated by the restored process")
    port_up: Optional[bool] = Field(
        default=None, description="Whether wait_port came up in time"
    )
    verified: Optional[bool] = Field(
        default=None, description="Whether a verification request succeeded"
    )


class CompressionResult(BaseModel):
    """Result of compress/decompress operations."""

    path: str = Field(description="Path to the compressed archive or decompressed dir")
    original_size: int = Field(description="Size before compression")
    compressed_size: int = Field(description="Size after compression")
    ratio: float = Field(description="compressed_size / original_size")
    duration_ms: int = Field(description="Wall-clock time of the operation")


class PreflightIssue(BaseModel):
    """A single problem found by preflight.check()."""

    severity: str = Field(description="'error' or 'warning'")
    component: str = Field(description="'criu' | 'cuda' | 'kernel' | 'caps' | 'gpu'")
    message: str = Field(description="Human-readable description of the issue")
    fix_command: Optional[str] = Field(
        default=None, description="Shell command that may fix it (or None)"
    )

    def __str__(self) -> str:
        prefix = "ERROR" if self.severity == "error" else "WARN"
        out = f"[{prefix}] {self.component}: {self.message}"
        if self.fix_command:
            out += f"\n  fix: {self.fix_command}"
        return out
