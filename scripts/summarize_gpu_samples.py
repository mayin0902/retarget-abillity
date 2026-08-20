from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--host-alias", required=True)
    parser.add_argument("--gpu-index", type=int, required=True)
    args = parser.parse_args()
    raw = args.input_csv.read_bytes()
    rows: list[dict[str, float]] = []
    with args.input_csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "timestamp_ms": float(row["timestamp_ms"].strip()),
                    "memory_mib": float(row["memory_mib"].strip()),
                    "utilization_percent": float(row["utilization_percent"].strip()),
                    "power_watts": float(row["power_watts"].strip()),
                }
            )
    if len(rows) < 2:
        raise ValueError("at least two GPU samples are required")
    total_seconds = 0.0
    active_seconds = 0.0
    total_wh = 0.0
    active_wh = 0.0
    active_power_weighted = 0.0
    for previous, current in zip(rows, rows[1:], strict=False):
        duration = min(2.5, max(0.0, (current["timestamp_ms"] - previous["timestamp_ms"]) / 1000))
        power = (previous["power_watts"] + current["power_watts"]) / 2
        total_seconds += duration
        total_wh += power * duration / 3600
        if current["utilization_percent"] > 0:
            active_seconds += duration
            active_wh += power * duration / 3600
            active_power_weighted += power * duration
    summary = {
        "schema_version": "1.0",
        "phase": args.phase,
        "host_alias": args.host_alias,
        "gpu_index": args.gpu_index,
        "sample_count": len(rows),
        "source_csv_sha256": hashlib.sha256(raw).hexdigest(),
        "capture_seconds": total_seconds,
        "active_gpu_seconds": active_seconds,
        "peak_memory_mib": max(row["memory_mib"] for row in rows),
        "peak_utilization_percent": max(row["utilization_percent"] for row in rows),
        "peak_power_watts": max(row["power_watts"] for row in rows),
        "active_power_mean_watts": (
            active_power_weighted / active_seconds if active_seconds else None
        ),
        "total_window_energy_wh": total_wh,
        "active_energy_wh": active_wh,
        "active_definition": "utilization_percent > 0; intervals capped at 2.5 seconds",
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
