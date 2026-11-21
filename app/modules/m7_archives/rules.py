import hashlib
import json

from app.reasons import sources
from app.errors import DomainError
from app.modules.m4_paths.rules import TIME_PENDING


def evaluate(rows, context):
    stages = [v for v in context.values() if isinstance(v, dict) and "metrics" in v]
    balances = [s["metrics"]["balanced"] for s in stages if "balanced" in s["metrics"]]
    if balances and not all(balances):
        raise DomainError("UNBALANCED", "来源行未对平，不能归档")
    ids = [str(r.get("parcel", r.get("_line", ""))) for r in rows]
    if len(ids) != len(set(ids)):
        raise DomainError("ARCHIVE_DUPLICATE", "归档业务键重复")
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode()
    pending = sum(r.get("path_decision") == TIME_PENDING for r in rows)
    return {
        "rows": rows,
        "quarantine": [],
        "metrics": {
            "records": len(rows),
            "source_lines": len(set(x for row in rows for x in sources(row))),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "ready": True,
            "formal_records": len(rows) - pending,
            "time_pending_sealed": pending,
            "differences": sum(r.get("status") == "差异" for r in rows),
        },
    }
