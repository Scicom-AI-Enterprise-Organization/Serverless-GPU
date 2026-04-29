from serverlessgpu import endpoint, QueueDepthAutoscaler


@endpoint(
    model="Qwen/Qwen2.5-7B-Instruct",
    gpu="H100",
    autoscaler=QueueDepthAutoscaler(
        max_containers=3,
        tasks_per_container=30,
        idle_timeout_s=300,
    ),
)
def qwen():
    """vLLM serves the model — this body is not executed by the platform."""
    pass
