// Display helper used everywhere we show a raw `gpu_type`. Each provider
// stores its native enum on the row (RunPod long-form like "NVIDIA H100 80GB
// HBM3", PI underscore form like "H100_80GB"), so this normalizes both into
// the same short label.
export function shortGpu(gpu: string | null | undefined): string {
  if (!gpu) return "";
  // PI enum: trailing `_<n>GB` is just the VRAM tag — drop it. Then space out
  // model+digit runs (RTX4090 → RTX 4090). Keep AMD/Intel ids untouched.
  if (/^[A-Z][A-Z0-9]*_\d+GB$/i.test(gpu) || /^[A-Z][A-Z0-9]*_(?:NVL_)?\d+GB$/i.test(gpu)) {
    const head = gpu.split("_")[0];
    return head.replace(/([A-Za-z]+)(\d.*)/, "$1 $2").trim();
  }
  // RunPod long form.
  return gpu
    .replace(/^NVIDIA\s+/i, "")
    .replace(/\s+GeForce\s+/i, " ")
    .replace(/^GeForce\s+/i, "")
    .replace(/\s+80GB\s+(HBM3|PCIe).*$/i, " 80GB");
}
