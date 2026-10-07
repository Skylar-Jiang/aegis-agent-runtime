"""Measure isolated evaluation worker peak RSS, NOT whole product memory."""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from .dataset import ROOT, load


def peak_rss_bytes():
    if os.name != "nt":
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value if sys.platform == "darwin" else value * 1024
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    *[(name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                        "PagefileUsage", "PeakPagefileUsage")]]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PeakWorkingSetSize


def worker(repeats):
    import asyncio
    from .evaluate import run_case
    cases = [c for c in load() if c["split"] == "dev"]
    async def workload():
        for _ in range(repeats):
            for case in cases:
                await run_case(case, "reference_full", 3)
    start = time.perf_counter()
    asyncio.run(workload())
    print(json.dumps({"trajectories": repeats * len(cases),
                      "elapsed_seconds": time.perf_counter() - start,
                      "worker_peak_rss_bytes": peak_rss_bytes()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / "resource-pilot.json")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
    else:
        rows = []
        for repeats in (1, 1, 10):
            start = time.perf_counter()
            result = subprocess.run([sys.executable, "-m", "experiments.intent.resources", "--worker", str(repeats)],
                                    cwd=ROOT.parents[1], capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise RuntimeError(result.stderr)
            rows.append({**json.loads(result.stdout), "cold_process_wall_seconds": time.perf_counter() - start})
        args.output.write_text(json.dumps({"scope": "isolated fixture evaluation Python worker only",
            "measurements": rows, "excluded": ["production detector/model", "server", "browser", "database", "subprocess peaks"],
            "units": "bytes; MB=1000000 bytes, MiB=1048576 bytes",
            "official_500M_compliance": "unverified; whole system and official scope absent"}, indent=2), encoding="utf-8")
        print(args.output)
