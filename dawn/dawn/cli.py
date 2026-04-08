"""dawn CLI — typer-based entry point.

Run `dawn --help` for the command list.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from dawn.core import checkpoint as ckpt_mod
from dawn.core import compression, gpu, pageserver as ps, process
from dawn.core.checkpoint import Checkpoint
from dawn.setup import installer, preflight

app = typer.Typer(
    name="dawn",
    help="Checkpoint/restore tool for serverless GPU inference",
    no_args_is_help=True,
)
console = Console()


# ─── helpers ────────────────────────────────────────────────────────────


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _print_json(obj) -> None:
    if hasattr(obj, "model_dump_json"):
        print(obj.model_dump_json(indent=2))
    else:
        print(json.dumps(obj, indent=2, default=str))


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ─── doctor ─────────────────────────────────────────────────────────────


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Check whether this system is ready to run dawn."""
    issues = preflight.check()

    if json_output:
        _print_json([i.model_dump() for i in issues])
        raise typer.Exit(0 if not any(i.severity == "error" for i in issues) else 1)

    if not issues:
        console.print("[green]:heavy_check_mark: All preflight checks passed[/green]")
        raise typer.Exit(0)

    table = Table(title="Preflight issues")
    table.add_column("Severity")
    table.add_column("Component")
    table.add_column("Message")
    table.add_column("Fix")
    for i in issues:
        color = "red" if i.severity == "error" else "yellow"
        table.add_row(
            f"[{color}]{i.severity.upper()}[/{color}]",
            i.component,
            i.message,
            i.fix_command or "",
        )
    console.print(table)

    error_count = sum(1 for i in issues if i.severity == "error")
    raise typer.Exit(1 if error_count else 0)


# ─── setup ──────────────────────────────────────────────────────────────


@app.command()
def setup(
    skip_apt: bool = typer.Option(False, "--skip-apt"),
    skip_criu: bool = typer.Option(False, "--skip-criu"),
    skip_cuda_checkpoint: bool = typer.Option(False, "--skip-cuda-checkpoint"),
    skip_sysctl: bool = typer.Option(False, "--skip-sysctl"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Install CRIU + cuda-checkpoint and configure the kernel. Requires root."""
    _setup_logging(verbose)
    try:
        installer.install_all(
            skip_apt=skip_apt,
            skip_criu=skip_criu,
            skip_cuda_checkpoint=skip_cuda_checkpoint,
            skip_sysctl=skip_sysctl,
        )
    except PermissionError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)
    console.print("[green]:heavy_check_mark: setup complete[/green]")


# ─── status / find / inspect ────────────────────────────────────────────


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show all CUDA processes and their checkpoint state."""
    cuda_pids = gpu.find_cuda_pids()

    if json_output:
        items = [
            {
                "pid": p,
                "state": gpu.get_state(p),
                "vram_mb_total": gpu.vram_used_mb(),
            }
            for p in cuda_pids
        ]
        _print_json({"cuda_processes": items, "vram_used_mb": gpu.vram_used_mb()})
        return

    if not cuda_pids:
        console.print("No CUDA processes found.")
        console.print(f"VRAM used: {gpu.vram_used_mb()} MiB")
        return

    table = Table(title="CUDA processes")
    table.add_column("PID")
    table.add_column("State")
    for pid in cuda_pids:
        table.add_row(str(pid), gpu.get_state(pid))
    console.print(table)
    console.print(f"VRAM used: {gpu.vram_used_mb()} MiB")


@app.command()
def find(
    needle: str = typer.Argument(..., help="Substring of the cmdline to search for"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Print only the PID"),
) -> None:
    """Find a process by cmdline substring (e.g. 'vllm serve')."""
    pid = process.find_pid_by_cmdline(needle)
    if pid is None:
        if not quiet:
            console.print(f"[red]no process found matching {needle!r}[/red]")
        raise typer.Exit(1)
    if quiet:
        print(pid)
    else:
        console.print(f"PID: [green]{pid}[/green]")


# ─── dump ───────────────────────────────────────────────────────────────


@app.command()
def dump(
    pid: Optional[int] = typer.Option(None, "--pid", help="Target PID"),
    vllm: bool = typer.Option(False, "--vllm", help="Auto-find vLLM main PID"),
    output: Path = typer.Option(..., "-o", "--output", help="Checkpoint output dir"),
    timeout: int = typer.Option(300, help="CRIU timeout in seconds"),
    page_server: Optional[str] = typer.Option(
        None, "--page-server", help="Stream pages to host:port instead of disk"
    ),
    compress: bool = typer.Option(False, "--compress", help="lz4-compress after dump"),
    compress_path: Optional[Path] = typer.Option(
        None, "--compress-path", help="Where to write the .tar.lz4"
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Checkpoint a running process tree."""
    _setup_logging(verbose)

    if not pid and not vllm:
        console.print("[red]must specify --pid or --vllm[/red]")
        raise typer.Exit(2)

    if vllm:
        pid = process.find_vllm_pid()
        if pid is None:
            console.print("[red]no vllm serve process found[/red]")
            raise typer.Exit(1)
        if not json_output:
            console.print(f"Found vLLM PID [green]{pid}[/green]")

    try:
        result = Checkpoint.dump(
            pid=pid,
            output_dir=output,
            timeout=timeout,
            page_server=page_server,
            compress=compress,
            compress_path=compress_path,
            verbose=4 if verbose else 0,
        )
    except Exception as e:
        console.print(f"[red]dump failed: {e}[/red]")
        raise typer.Exit(1)

    if json_output:
        _print_json(result)
        return

    console.print()
    console.print("[green]:heavy_check_mark: Dump complete[/green]")
    console.print(f"  Path:        {result.path}")
    console.print(f"  Size:        {_human_size(result.size_bytes)}")
    console.print(f"  Files:       {result.file_count}")
    console.print(f"  Duration:    {result.duration_ms} ms")
    console.print(f"  PID:         {result.pid}")
    console.print(f"  GPU before:  {result.gpu_state_before}")
    console.print(f"  GPU after:   {result.gpu_state_after}")


# ─── restore ────────────────────────────────────────────────────────────


@app.command()
def restore(
    checkpoint_dir: Path = typer.Argument(..., help="Checkpoint directory or .tar.lz4 archive"),
    pre_warm: bool = typer.Option(False, "--pre-warm", help="Pre-warm OS page cache before restore"),
    wait_port: Optional[int] = typer.Option(None, "--wait-port", help="Wait for this TCP port to come up"),
    wait_timeout: float = typer.Option(60.0, "--wait-timeout"),
    verify_request: bool = typer.Option(False, "--verify-request", help="Hit /v1/models after port is up"),
    timeout: int = typer.Option(300, help="CRIU timeout in seconds"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Restore a checkpoint."""
    _setup_logging(verbose)

    try:
        result = Checkpoint.restore(
            checkpoint_dir=checkpoint_dir,
            timeout=timeout,
            pre_warm=pre_warm,
            wait_port=wait_port,
            wait_timeout=wait_timeout,
            verify_request=verify_request,
            verbose=4 if verbose else 0,
        )
    except Exception as e:
        console.print(f"[red]restore failed: {e}[/red]")
        raise typer.Exit(1)

    if json_output:
        _print_json(result)
        return

    console.print()
    console.print("[green]:heavy_check_mark: Restore complete[/green]")
    console.print(f"  Duration:    {result.duration_ms} ms")
    console.print(f"  PID:         {result.pid}")
    console.print(f"  GPU state:   {result.gpu_state}")
    console.print(f"  VRAM:        {result.vram_mb} MiB")
    if result.port_up is not None:
        console.print(f"  Port up:     {result.port_up}")
    if result.verified is not None:
        console.print(f"  Verified:    {result.verified}")


# ─── compress / decompress ──────────────────────────────────────────────


@app.command()
def compress(
    src: Path = typer.Argument(..., help="Checkpoint directory"),
    dst: Path = typer.Argument(..., help="Output .tar.lz4 path"),
    level: int = typer.Option(1, "--level", min=1, max=9, help="lz4 level"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """lz4-compress a checkpoint directory."""
    try:
        result = compression.compress(src, dst, level=level)
    except Exception as e:
        console.print(f"[red]compress failed: {e}[/red]")
        raise typer.Exit(1)

    if json_output:
        _print_json(result)
        return

    console.print(f"[green]:heavy_check_mark: Compressed[/green]")
    console.print(f"  Path:        {result.path}")
    console.print(f"  Original:    {_human_size(result.original_size)}")
    console.print(f"  Compressed:  {_human_size(result.compressed_size)}")
    console.print(f"  Ratio:       {result.ratio*100:.1f}%")
    console.print(f"  Duration:    {result.duration_ms} ms")


@app.command()
def decompress(
    src: Path = typer.Argument(..., help="Source .tar.lz4 archive"),
    dst: Path = typer.Argument(..., help="Output directory"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Decompress a .tar.lz4 archive into a directory."""
    try:
        result = compression.decompress(src, dst)
    except Exception as e:
        console.print(f"[red]decompress failed: {e}[/red]")
        raise typer.Exit(1)

    if json_output:
        _print_json(result)
        return

    console.print(f"[green]:heavy_check_mark: Decompressed[/green]")
    console.print(f"  Path:        {result.path}")
    console.print(f"  Compressed:  {_human_size(result.compressed_size)}")
    console.print(f"  Decompressed:{_human_size(result.original_size)}")
    console.print(f"  Duration:    {result.duration_ms} ms")


# ─── page-server ────────────────────────────────────────────────────────


@app.command("page-server")
def page_server(
    output: Path = typer.Option(..., "-o", "--output", help="Where to write incoming pages"),
    listen_port: int = typer.Option(27, "--port", help="Port to listen on"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Block until transfer completes"),
) -> None:
    """Start a CRIU page-server (receives streamed pages from another node)."""
    try:
        server = ps.start(output, port=listen_port)
    except Exception as e:
        console.print(f"[red]page-server start failed: {e}[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]page-server running[/green] pid={server.pid} port={server.port} output={server.output_dir}"
    )
    if wait:
        console.print("Waiting for transfer to complete (Ctrl-C to stop)...")
        if server.wait():
            console.print("[green]:heavy_check_mark: transfer complete[/green]")
        else:
            console.print("[yellow]timed out waiting[/yellow]")


# ─── verify ─────────────────────────────────────────────────────────────


@app.command()
def verify(
    checkpoint_dir: Path = typer.Argument(...),
) -> None:
    """Verify a checkpoint exists and looks valid (does not actually restore)."""
    if not checkpoint_dir.is_dir():
        console.print(f"[red]not a directory: {checkpoint_dir}[/red]")
        raise typer.Exit(1)

    files = list(checkpoint_dir.iterdir())
    img_files = [f for f in files if f.suffix == ".img"]
    pages_files = [f for f in files if f.name.startswith("pages-")]

    if not img_files:
        console.print("[red]no .img files found — not a CRIU checkpoint[/red]")
        raise typer.Exit(1)

    total_size = sum(f.stat().st_size for f in files if f.is_file())
    console.print(f"[green]:heavy_check_mark: Looks valid[/green]")
    console.print(f"  Files:       {len(files)}")
    console.print(f"  .img files:  {len(img_files)}")
    console.print(f"  Page files:  {len(pages_files)}")
    console.print(f"  Total size:  {_human_size(total_size)}")


if __name__ == "__main__":
    app()
