import importlib.util
import os
import sys
from pathlib import Path

import httpx
import typer
from rich import print
from rich.table import Table

from .decorators import EndpointSpec

app = typer.Typer(help="Serverless-GPU CLI", no_args_is_help=True)


def _gateway_url() -> str:
    return os.environ.get("SERVERLESSGPU_GATEWAY", "http://localhost:8080")


def _auth_headers() -> dict:
    """Build the Authorization header from SERVERLESSGPU_API_KEY (if set).

    Empty when the env var isn't set — the gateway's auth dep is also a no-op
    when GATEWAY_API_KEYS is empty, so dev / fakeredis flows just work.
    """
    key = os.environ.get("SERVERLESSGPU_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _load_spec(target: str) -> EndpointSpec:
    if ":" not in target:
        raise typer.BadParameter("target must be 'path/to/file.py:function'")
    file_str, func_name = target.split(":", 1)
    file = Path(file_str).resolve()
    if not file.exists():
        raise typer.BadParameter(f"file not found: {file}")

    module_name = f"_serverlessgpu_user_{file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, file)
    if spec is None or spec.loader is None:
        raise typer.BadParameter(f"could not load module from {file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    fn = getattr(module, func_name, None)
    if fn is None:
        raise typer.BadParameter(f"no function '{func_name}' in {file}")

    endpoint_spec = getattr(fn, "__serverlessgpu_spec__", None)
    if endpoint_spec is None:
        raise typer.BadParameter(f"'{func_name}' is not decorated with @endpoint")
    return endpoint_spec


@app.command()
def deploy(
    target: str = typer.Argument(..., help="Path:function, e.g. app.py:qwen"),
    name: str = typer.Option(None, "--name", help="Override the endpoint name"),
):
    """Deploy an @endpoint-decorated function to the gateway."""
    spec = _load_spec(target)
    if name:
        spec.name = name

    body = {
        "name": spec.name,
        "model": spec.model,
        "gpu": spec.gpu,
        "autoscaler": {
            "max_containers": spec.autoscaler.max_containers,
            "tasks_per_container": spec.autoscaler.tasks_per_container,
            "idle_timeout_s": spec.autoscaler.idle_timeout_s,
        },
    }

    gateway = _gateway_url()
    url = f"{gateway}/apps"
    try:
        r = httpx.post(url, json=body, headers=_auth_headers(), timeout=10.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[red]deploy failed:[/red] {e}")
        raise typer.Exit(code=1)

    data = r.json()
    app_id = data["app_id"]
    has_auth = bool(os.environ.get("SERVERLESSGPU_API_KEY"))

    print(f"[green]Deployed[/green] [bold]{app_id}[/bold] ({spec.model} on {spec.gpu})")
    print()
    print("[bold]OpenAI-compatible:[/bold]   [dim]# any OpenAI client works[/dim]")
    print(f"  POST   {gateway}/v1/chat/completions   model: \"{app_id}\"")
    print(f"  POST   {gateway}/v1/completions        model: \"{app_id}\"")
    print(f"  POST   {gateway}/v1/embeddings         model: \"{app_id}\"")
    print()
    print("[bold]Native (async-poll + SSE):[/bold]")
    print(f"  POST   {gateway}/run/{app_id}     [dim]# returns request_id; poll /result[/dim]")
    print(f"  POST   {gateway}/stream/{app_id}  [dim]# SSE, streams tokens live[/dim]")
    print()
    print("[bold]Try it:[/bold]")
    auth_line = ' \\\n       -H "Authorization: Bearer $SERVERLESSGPU_API_KEY"' if has_auth else ""
    print(f"  curl {gateway}/v1/chat/completions \\")
    print(f"       -H 'content-type: application/json'{auth_line} \\")
    print(f"       -d '{{\"model\": \"{app_id}\", \"messages\": [{{\"role\": \"user\", \"content\": \"hello\"}}]}}'")
    print()
    print("  [dim]or with the OpenAI SDK:[/dim]")
    print(f"  [dim]openai.OpenAI(base_url='{gateway}/v1', api_key=...).chat.completions.create(model='{app_id}', ...)[/dim]")


@app.command()
def run(
    app_id: str = typer.Argument(..., help="App id (the deploy name)"),
    payload: str = typer.Option("{}", "--payload", "-d", help="JSON payload to send"),
    timeout: int = typer.Option(
        600,
        "--timeout",
        "-t",
        help="Seconds to wait for the result before giving up. Default 600s "
        "covers cold-start (PI provision + vLLM model load) for first request.",
    ),
):
    """Send one request to a deployed app and poll for the result.

    Note: cold start to a fresh PI worker can take 60-90s (provision + image pull
    + model weights to GPU). Don't lower --timeout below ~120s unless your worker
    is already warm.
    """
    import json
    import time

    gateway = _gateway_url()
    body = json.loads(payload)

    headers = _auth_headers()
    r = httpx.post(f"{gateway}/run/{app_id}", json=body, headers=headers, timeout=10.0)
    r.raise_for_status()
    request_id = r.json()["request_id"]
    print(f"[cyan]request_id:[/cyan] {request_id} [dim](timeout {timeout}s)[/dim]")

    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        r = httpx.get(f"{gateway}/result/{request_id}", headers=headers, timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            status = data.get("status")
            # Surface mid-flight status changes once each — useful UX during
            # the long initial cold-start poll loop.
            if status != last_status:
                if status == "pending":
                    print("[dim]waiting for worker...[/dim]")
                last_status = status
            if status == "completed":
                print("[green]completed[/green]")
                print(json.dumps(data.get("output"), indent=2))
                return
            if status == "timeout":
                print(f"[red]worker reported timeout:[/red] {data.get('output')}")
                raise typer.Exit(code=1)
            if status == "cancelled":
                print(f"[yellow]cancelled:[/yellow] {data.get('output')}")
                raise typer.Exit(code=1)
        time.sleep(0.3)

    print(
        f"[red]CLI timeout after {timeout}s.[/red] The request may still complete "
        f"server-side — check via:\n"
        f"  curl {gateway}/result/{request_id}"
    )
    raise typer.Exit(code=1)


@app.command()
def stream(
    app_id: str = typer.Argument(..., help="App id"),
    payload: str = typer.Option("{}", "--payload", "-d", help="JSON payload to send"),
):
    """Open an SSE stream — see token chunks live as the model emits them."""
    import json as _json
    gateway = _gateway_url()
    body = _json.loads(payload)

    with httpx.stream("POST", f"{gateway}/stream/{app_id}", json=body, headers=_auth_headers(), timeout=300.0) as r:
        if r.status_code != 200:
            print(f"[red]stream failed:[/red] {r.status_code} {r.read().decode()}")
            raise typer.Exit(code=1)
        request_id = r.headers.get("X-Request-Id", "?")
        print(f"[cyan]request_id:[/cyan] {request_id}")

        buf = ""
        for raw in r.iter_text():
            buf += raw
            while "\n\n" in buf:
                evt, buf = buf.split("\n\n", 1)
                for line in evt.split("\n"):
                    if line.startswith("data: "):
                        try:
                            chunk = _json.loads(line[6:])
                        except _json.JSONDecodeError:
                            continue
                        if chunk.get("done"):
                            print()
                            print("[green]done[/green]")
                            return
                        if chunk.get("error"):
                            print(f"[red]error:[/red] {chunk['error']}")
                            return
                        if "delta" in chunk:
                            # token-by-token print without newlines
                            print(chunk["delta"], end="", flush=True)


@app.command("list")
def list_apps():
    """List all deployed apps on the gateway."""
    gateway = _gateway_url()
    r = httpx.get(f"{gateway}/apps", headers=_auth_headers(), timeout=5.0)
    r.raise_for_status()
    apps = r.json()
    if not apps:
        print("[yellow]no apps deployed[/yellow]")
        return

    table = Table(title="Deployed apps")
    table.add_column("name")
    table.add_column("model")
    table.add_column("gpu")
    table.add_column("max_containers")
    table.add_column("tasks/container")
    table.add_column("idle_s")
    for a in apps:
        cfg = a.get("autoscaler", {})
        table.add_row(
            a["name"],
            a["model"],
            a["gpu"],
            str(cfg.get("max_containers", "?")),
            str(cfg.get("tasks_per_container", "?")),
            str(cfg.get("idle_timeout_s", "?")),
        )
    print(table)


@app.command()
def delete(
    app_id: str = typer.Argument(..., help="App id to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Tear down an app: drain its workers, kill its queue, remove the record."""
    gateway = _gateway_url()
    if not yes:
        confirm = typer.prompt(
            f"Delete '{app_id}'? Workers will be drained and the queue removed. [y/N]",
            default="N",
        )
        if confirm.lower() not in ("y", "yes"):
            print("aborted")
            raise typer.Exit(code=0)

    r = httpx.delete(f"{gateway}/apps/{app_id}", headers=_auth_headers(), timeout=10.0)
    if r.status_code == 404:
        print(f"[yellow]app not found:[/yellow] {app_id}")
        raise typer.Exit(code=1)
    r.raise_for_status()
    data = r.json()
    print(f"[green]deleted[/green] {app_id} (drained {data['drained_workers']} workers)")


@app.command()
def show(app_id: str = typer.Argument(..., help="App id")):
    """Show the spec for a deployed app."""
    gateway = _gateway_url()
    r = httpx.get(f"{gateway}/apps/{app_id}", headers=_auth_headers(), timeout=5.0)
    if r.status_code == 404:
        print(f"[red]app not found:[/red] {app_id}")
        raise typer.Exit(code=1)
    r.raise_for_status()
    data = r.json()

    table = Table(title=f"App: {app_id}")
    table.add_column("Field")
    table.add_column("Value")
    for k, v in data.items():
        table.add_row(str(k), str(v))
    print(table)


@app.command("pi-check")
def pi_check(
    api_key: str = typer.Option(
        None,
        "--api-key",
        envvar="PI_API_KEY",
        help="PI API key (defaults to $PI_API_KEY)",
    ),
    api_base: str = typer.Option(
        "https://api.primeintellect.ai",
        "--api-base",
        envvar="PI_API_BASE",
    ),
):
    """Pre-flight: verify your Prime Intellect API key works and list pods on the account."""
    if not api_key:
        print("[red]missing PI_API_KEY[/red] — pass --api-key or set the env var")
        raise typer.Exit(code=1)

    if api_key.startswith("pit_") and len(api_key) > 16:
        masked = api_key[:6] + "..." + api_key[-4:]
    else:
        masked = "<unrecognized format>"
    print(f"[cyan]checking PI[/cyan] key={masked} base={api_base}")

    try:
        r = httpx.get(
            f"{api_base.rstrip('/')}/api/v1/pods/",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"offset": 0, "limit": 100},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        print(f"[red]connection failed:[/red] {e}")
        raise typer.Exit(code=1)

    if r.status_code == 401:
        print("[red]401 unauthorized[/red] - key is invalid or revoked")
        raise typer.Exit(code=1)
    if r.status_code >= 400:
        print(f"[red]PI returned {r.status_code}:[/red] {r.text[:200]}")
        raise typer.Exit(code=1)

    data = r.json()
    pods = data.get("data", [])
    total = data.get("total_count", len(pods))

    print(f"[green]key works[/green] - {total} pod(s) on this account")
    if not pods:
        print("  (no pods currently running)")
        print(
            "\n[yellow]reminder:[/yellow] PI's API has no list-templates endpoint. "
            "Copy your custom template id from the PI dashboard and set "
            "[bold]PI_CUSTOM_TEMPLATE_ID[/bold] before enabling the autoscaler."
        )
        return

    table = Table(title="Pods on your PI account")
    table.add_column("id")
    table.add_column("name")
    table.add_column("status")
    table.add_column("gpu")
    table.add_column("ip")
    table.add_column("$/hr")
    for pod in pods[:20]:
        table.add_row(
            str(pod.get("id", ""))[:24],
            str(pod.get("name", ""))[:40],
            str(pod.get("status", "")),
            f"{pod.get('gpuName', '')} x{pod.get('gpuCount', '')}",
            str(pod.get("ip", "")),
            str(pod.get("priceHr", "")),
        )
    print(table)
    if total > 20:
        print(f"  ...and {total - 20} more")


if __name__ == "__main__":
    app()
