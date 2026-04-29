from dataclasses import dataclass, field, asdict
from typing import Callable, Optional


@dataclass
class QueueDepthAutoscaler:
    max_containers: int = 1
    tasks_per_container: int = 30
    idle_timeout_s: int = 300


@dataclass
class EndpointSpec:
    model: str
    gpu: str
    autoscaler: QueueDepthAutoscaler = field(default_factory=QueueDepthAutoscaler)
    name: Optional[str] = None
    cpu: int = 2
    memory: str = "16Gi"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def endpoint(
    *,
    model: str,
    gpu: str,
    autoscaler: Optional[QueueDepthAutoscaler] = None,
    name: Optional[str] = None,
    cpu: int = 2,
    memory: str = "16Gi",
) -> Callable:
    """Mark a function as a deployable inference endpoint.

    The function body is intentionally not executed by the platform — the model is
    served by vLLM directly. The decorated function exists so tooling can locate
    the spec via `module:func` and the developer has somewhere to attach docs.
    """
    def wrap(fn: Callable) -> Callable:
        spec = EndpointSpec(
            model=model,
            gpu=gpu,
            autoscaler=autoscaler or QueueDepthAutoscaler(),
            name=name or fn.__name__.replace("_", "-"),
            cpu=cpu,
            memory=memory,
        )
        fn.__serverlessgpu_spec__ = spec
        return fn

    return wrap
