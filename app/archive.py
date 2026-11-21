import csv
import io
import json

from app.modules.m4_paths.rules import TIME_PENDING


def build_archive(run):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["run_id", "input_sha256", "rule_version", "kind",
                     "binding", "source_lines", "sla", "reason", "evidence"])
    meta = run["manifest"]
    for row in run["stages"][-1]["rows"]:
        is_pending = row.get("path_decision") == TIME_PENDING
        kind = "time_pending" if is_pending else "accepted"
        binding = "临时" if is_pending else "正式"
        writer.writerow([
            run["run_id"], meta["input_sha256"], meta["rule_version"], kind,
            binding, json.dumps(row.get("_sources", [])),
            row.get("sla", ""), row.get("time_reason", row.get("status", "")),
            json.dumps(row, ensure_ascii=False),
        ])
    for row in run["stages"][-1].get("cumulative_quarantine", []):
        writer.writerow([
            run["run_id"], meta["input_sha256"], meta["rule_version"], "quarantine",
            "隔离", json.dumps(row.get("_sources", [])),
            "", row.get("reason", ""), json.dumps(row, ensure_ascii=False),
        ])
    return "﻿" + stream.getvalue()
