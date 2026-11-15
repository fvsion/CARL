"""The GPU inventory's wiring (W6.1): the sources of each OS, the GPU settings from the environment and config.json,
the pids of CARL's own servers, and one inventory per process (the GPU limit, `carl fit` and `carl gpu` share it).

  windows   nvidia-smi, then the Windows GPU counters (D3DKMT and PDH, adapters/windows/gpu_counters.py); its own
            servers' VRAM from the per-process counter; llama-server's list without its "free" (wrong there)
  linux     nvidia-smi, then the amdgpu sysfs files; its own servers' VRAM from nvidia-smi's compute apps and
            amdgpu's fdinfo; llama-server's list

CARL's own server is the process listening on the port of a server-PORT.json in the settings folder (the start
writes it, the stop removes it), and the llama-server processes it started: a router's model servers (W1.6:
without them the room fell by the loaded model in router mode).
"""
from __future__ import annotations

import os
import re
from typing import Callable, Dict, List, Mapping, Tuple

from ..domain.config.settings import GPU_KEYS
from ..domain.hardware.gpu import CUDA_ORDER, VISIBILITY
from ..domain.memory.budget import GpuSettings, gpu_settings
from ..domain.shared.errors import ConfigError

# The variables the GPU adapters read: the GPU settings' (the schema names them) and the visibility variables.
GPU_VARS = tuple(str(s.env) for s in GPU_KEYS.values() if s.env) + VISIBILITY
_SERVER_FILE = re.compile(r"^server-(\d+)\.json$")
_SHARED: Dict[Tuple[str, object], object] = {}


def env_values(env: Mapping[str, str]) -> Tuple[Tuple[str, str], ...]:
    """The GPU variables that are set, for Machine.gpu_env."""
    return tuple((k, env[k]) for k in GPU_VARS if k in env)


def settings_reader(env: Mapping[str, str], conf_dir: str) -> Callable[[], GpuSettings]:
    """Reads the GPU settings: the variables, then config.json's gpu section (read again at each call)."""
    from ..adapters.common.json_files import read_json
    path = os.path.join(conf_dir, "config.json") if conf_dir else ""

    def read() -> GpuSettings:
        try:
            raw = read_json(path) if path else None
        except ConfigError:
            raw = None
        return gpu_settings(env, raw.get("gpu") if isinstance(raw, dict) else None)
    return read


def own_pids(os_name: str, conf_dir: str) -> Callable[[], List[int]]:
    """The pids of CARL's own llama-servers: the listeners on the ports of the settings folder's server files and the
    llama-servers they started (a router's model servers, W1.6)."""
    def pids() -> List[int]:
        try:
            ports = [int(m.group(1)) for m in map(_SERVER_FILE.match, os.listdir(conf_dir)) if m] if conf_dir else []
        except OSError:
            ports = []
        if not ports:
            return []
        from ..domain.hardware.processes import own_servers
        if os_name == "windows":
            from ..adapters.windows.processes import snapshot_tree
            from ..adapters.windows.tcp_table import listeners
            table = listeners()
            return own_servers([table[p] for p in ports if p in table], snapshot_tree() or [])
        from ..adapters.linux.ss_proc import ProcPorts, process_tree
        probe = ProcPorts()
        return own_servers([pid for pid in (probe.owner(p) for p in ports) if pid], process_tree())
    return pids


def sources(os_name: str, llama_dir: object) -> Tuple[List[object], object, List[object]]:
    """(the sources, the fallback, the sources of a process's VRAM) of an OS."""
    from ..adapters.gpu.inventory import llama_program
    from ..adapters.gpu.llama_devices import LlamaDevices
    from ..adapters.gpu.tool import run_logged
    from ..adapters.gpu.nvidia_smi import NvidiaSmi
    folder = llama_dir if isinstance(llama_dir, str) else None
    pci = {**os.environ, CUDA_ORDER[0]: CUDA_ORDER[1]}            # the server's CUDA numbers are CARL's: PCI bus order
    ending = ".exe" if os_name == "windows" else ""
    listed = LlamaDevices(lambda: llama_program(folder=folder, exe=ending), free_known=os_name != "windows",
                          run=lambda args, timeout: run_logged(args, timeout, pci))
    if os_name == "windows":
        from ..adapters.gpu.windows_counters import WindowsCounters
        from ..adapters.windows import gpu_counters
        counters = WindowsCounters(gpu_counters.adapters, gpu_counters.counter_values)
        return [NvidiaSmi(), counters], listed, [counters]
    from ..adapters.gpu.amdgpu_sysfs import AmdgpuSysfs
    smi, sysfs = NvidiaSmi(), AmdgpuSysfs()
    return [smi, sysfs], listed, [smi, sysfs]


def ram_reader(os_name: str) -> Callable[[], int]:
    """The reader of the installed RAM of a PC with this OS (0 when it is not known)."""
    if os_name == "windows":
        from ..adapters.windows.host import ram_bytes
        return ram_bytes
    if os_name == "linux":
        from ..adapters.linux.host import ram_bytes as linux_ram_bytes
        return linux_ram_bytes
    return lambda: 0


def limit(os_name: str, llama_dir: object, rocm: bool, gpu_env: Mapping[str, str], conf_dir: str) -> object:
    """The GPU limit of a PC (DeviceGpuLimit over its inventory), one per process for the same machine."""
    key = (os_name, (llama_dir, rocm, tuple(sorted(gpu_env.items())), conf_dir))
    if key not in _SHARED:
        from ..adapters.gpu.inventory import DeviceGpuLimit, Inventory, OwnServers
        found, fallback, own = sources(os_name, llama_dir)
        inventory = Inventory(found, fallback, settings_reader(gpu_env, conf_dir), gpu_env, rocm)
        _SHARED[key] = DeviceGpuLimit(inventory, OwnServers(inventory, own), own_pids(os_name, conf_dir),
                                      ram_reader(os_name))
    return _SHARED[key]
