"""Basic health + PC status (plan §31, §42: CPU/GPU/RAM/disk status)."""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

NVIDIA_SMI_QUERY = "name,utilization.gpu,memory.used,memory.total,temperature.gpu"


@dataclass(frozen=True)
class GpuStatus:
    name: str
    util_percent: float
    mem_used_mb: float
    mem_total_mb: float
    temp_c: float


@dataclass(frozen=True)
class PcStatus:
    cpu_percent: float
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disk_free_gb: float
    disk_total_gb: float
    boot_time: float
    gpus: list[GpuStatus]


async def _gpu_status() -> list[GpuStatus]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, f"--query-gpu={NVIDIA_SMI_QUERY}", "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except (OSError, TimeoutError):
        return []
    gpus = []
    for line in out.decode(errors="replace").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            gpus.append(GpuStatus(parts[0], *(float(p) for p in parts[1:])))
        except ValueError:
            continue
    return gpus


async def pc_status(disk_path: Path) -> PcStatus:
    cpu = await asyncio.to_thread(psutil.cpu_percent, 0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(disk_path.anchor or disk_path))
    gb = 1024 ** 3
    return PcStatus(
        cpu_percent=cpu,
        ram_percent=mem.percent,
        ram_used_gb=(mem.total - mem.available) / gb,
        ram_total_gb=mem.total / gb,
        disk_free_gb=disk.free / gb,
        disk_total_gb=disk.total / gb,
        boot_time=psutil.boot_time(),
        gpus=await _gpu_status(),
    )


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"


def format_pc_status(s: PcStatus) -> str:
    lines = [
        f"CPU: {s.cpu_percent:.0f}%",
        f"RAM: {s.ram_percent:.0f}% ({s.ram_used_gb:.1f}/{s.ram_total_gb:.1f} GB)",
        f"Disk free: {s.disk_free_gb:.0f}/{s.disk_total_gb:.0f} GB",
    ]
    for g in s.gpus:
        lines.append(
            f"GPU: {g.name} — {g.util_percent:.0f}%, "
            f"VRAM {g.mem_used_mb / 1024:.1f}/{g.mem_total_mb / 1024:.1f} GB, {g.temp_c:.0f}°C"
        )
    if not s.gpus:
        lines.append("GPU: তথ্য পাওয়া যায়নি")
    lines.append(f"PC uptime: {format_duration(time.time() - s.boot_time)}")
    return "\n".join(lines)
