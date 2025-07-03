import subprocess
import json
import shutil
import time

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich import box
import psutil

console = Console()

__version__ = "0.1"
__package_name__ = "GPUTOP"


def run_cmd(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.stdout.strip()
    except Exception:
        return None


def detect_tools():
    return {
        "nvidia": shutil.which("nvidia-smi") is not None,
        "amd": shutil.which("rocm-smi") is not None,
        "intel": shutil.which("intel_gpu_top") is not None,
    }

def get_versions(tools):
    version_info = {
        "tool": f"{__package_name__} {__version__}",
        "driver": "-",
        "runtime": "-"
    }

    if tools["nvidia"]:
        driver_output = run_cmd("nvidia-smi --query-gpu=driver_version --format=csv,noheader")
        smi_output = run_cmd("nvidia-smi")
        cuda_version = "-"
        if smi_output:
            for line in smi_output.splitlines():
                if "CUDA Version" in line:
                    parts = line.split("CUDA Version:")
                    if len(parts) == 2:
                        cuda_version = parts[1].strip().split()[0]
                    break
        version_info["driver"] = f"Driver Version {driver_output}" if driver_output else "-"
        version_info["runtime"] = f"CUDA {cuda_version}"

    elif tools["amd"]:
        amd_output = run_cmd("amd-smi version --json")
        if amd_output:
            try:
                data = json.loads(amd_output)[0]
                driver = data.get("amdgpu_version", "-")
                rocm = data.get("rocm_version", "-")
                version_info["driver"] = f"Driver Version {driver}"
                version_info["runtime"] = f"ROCm {rocm}"
            except Exception:
                version_info["driver"] = "-"
                version_info["runtime"] = "-"
        else:
            version_info["driver"] = "-"
            version_info["runtime"] = "-"

    elif tools["intel"]:
        version_info["driver"] = "Driver Version Unknown"
        version_info["runtime"] = "XPU Runtime"

    return version_info


def create_info_panel(versions):
    # Здесь делаем три колонки
    table = Table.grid(expand=True)
    table.add_column(justify="left", ratio=1)
    table.add_column(justify="center", ratio=1)
    table.add_column(justify="right", ratio=1)

    table.add_row(
        f"[bold green]{versions['tool']}[/]",
        f"[yellow]{versions['driver']}[/]",
        f"[cyan]{versions['runtime']}[/]"
    )

    return Panel(
        table,
        title="Info",
        box=box.SQUARE,
    )


# NVIDIA
def parse_nvidia():
    cmd = (
        "nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu,"
        "temperature.gpu,power.draw --format=csv,noheader,nounits"
    )
    output = run_cmd(cmd)
    if not output:
        return []
    rows = []
    for line in output.splitlines():
        parts = [p.strip() for p in line.split(",")]
        rows.append({
            "name": parts[0],
            "mem_used": int(parts[1]),
            "mem_total": int(parts[2]),
            "gpu_util": float(parts[3]),
            "temp": float(parts[4]),
            "power": float(parts[5])
        })
    return rows


# AMD
def parse_amd():
    cmd = "rocm-smi --showproductname --showuse --showtemp --showpower --showmeminfo vram --json"
    output = run_cmd(cmd)
    if not output:
        return []
    data = json.loads(output)
    rows = []
    for card, info in data.items():
        rows.append({
            "name": info.get("Card Series", "Unknown"),
            "gpu_util": float(info.get("GPU use (%)", "0")),
            "temp": float(info.get("Temperature (Sensor edge) (C)", "0")),
            "power": float(info.get("Current Socket Graphics Package Power (W)", "0")),
            "mem_used": int(info.get("VRAM Total Used Memory (B)", 0)) // (1024 ** 2),
            "mem_total": int(info.get("VRAM Total Memory (B)", 0)) // (1024 ** 2)
        })
    return rows


# Intel
def parse_intel():
    cmd = "intel_gpu_top -J -s 500 -d 1"
    output = run_cmd(cmd)
    if not output:
        return []
    try:
        data = json.loads(output)
        render_busy = data["engines"].get("Render/3D/0", {}).get("busy", 0)
        return [{
            "name": "Intel GPU",
            "gpu_util": int(render_busy),
            "temp": int(data.get("temperature", 0)),
            "power": float(data.get("power", 0)),
            "mem_used": 0,
            "mem_total": 0
        }]
    except Exception:
        return []


# NVIDIA GPU Processes
def get_nvidia_processes():
    cmd = "nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits"
    output = run_cmd(cmd)
    if not output:
        return []
    processes = []
    for line in output.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        pid, name, mem = parts
        processes.append({
            "pid": pid,
            "name": name,
            "mem": mem
        })
    return processes


def get_amd_processes():
    cmd = "rocm-smi --showpids --csv"
    output = run_cmd(cmd)
    procs = []

    if not output:
        return procs

    lines = output.splitlines()
    for line in lines:
        line = line.strip()
        if line.startswith('"PID'):
            try:
                key, val = line.split(",", 1)
                pid = key.replace("PID", "").strip().strip('"')
                val = val.strip().strip('"')
                parts = [p.strip() for p in val.split(",")]
                if len(parts) >= 3:
                    name = parts[0]
                    mem_bytes = int(parts[2])
                    mem_mib = mem_bytes // (1024 ** 2)
                    procs.append({
                        "pid": pid,
                        "name": name,
                        "mem": str(mem_mib)
                    })
            except Exception:
                continue
    return procs


def create_table(gpu_data, gpu_processes):
    title = Text("")

    table = Table(title=title, box=box.SQUARE, expand=True)
    table.add_column("GPU")
    table.add_column("Usage %")
    table.add_column("Temp °C")
    table.add_column("Power W")
    table.add_column("Memory Used")

    for gpu in gpu_data:
        mem_str = f"{gpu['mem_used']} / {gpu['mem_total']} MiB" if gpu['mem_total'] else "-"
        table.add_row(
            gpu["name"],
            str(gpu["gpu_util"]),
            str(gpu["temp"]),
            f"{gpu['power']:.1f}",
            mem_str
        )

    # CPU/RAM
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory()
    info_panel = Panel(
        f"[cyan]CPU Load:[/] {cpu}%\n[cyan]RAM Used:[/] {ram.used//(1024**2)} MiB / {ram.total//(1024**2)} MiB",
        title="",
        box=box.SQUARE,
    )

    # GPU Processes
    process_table = Table(box=box.SIMPLE, expand=True)
    process_table.add_column("PID", style="cyan")
    process_table.add_column("Name", style="magenta")
    process_table.add_column("GPU Mem (MiB)", justify="right")

    if gpu_processes:
        for proc in gpu_processes:
            process_table.add_row(proc["pid"], proc["name"], proc["mem"])
    else:
        process_table.add_row("-", "No GPU processes", "-")

    process_panel = Panel(
        process_table,
        title="",
        box=box.SQUARE,
        padding=(0, 0)
    )

    return table, info_panel, process_panel


def main():
    tools = detect_tools()
    versions = get_versions(tools)

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        while True:
            gpus = []
            if tools["nvidia"]:
                gpus.extend(parse_nvidia())
            if tools["amd"]:
                gpus.extend(parse_amd())
            if tools["intel"]:
                gpus.extend(parse_intel())

            processes = []
            if tools["nvidia"]:
                processes += get_nvidia_processes()
            if tools["amd"]:
                processes += get_amd_processes()
            if tools["intel"]:
                processes += []

            # avg_util = sum([g["gpu_util"] for g in gpus]) / max(1, len(gpus))
            # history.append(avg_util)

            table, info, proc_table = create_table(gpus, processes)
            layout = Table.grid(expand=True)
            layout.add_row(create_info_panel(versions))
            layout.add_row(table)
            layout.add_row(info)
            layout.add_row(proc_table)

            live.update(layout)
            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.clear()
