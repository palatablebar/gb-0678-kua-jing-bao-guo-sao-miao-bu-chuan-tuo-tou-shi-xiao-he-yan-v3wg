from decimal import Decimal

from app.reasons import sources
from app.errors import DomainError
from app.modules.m4_paths.rules import TIME_PENDING


def evaluate(rows, context):
    intake = context.get("intake")
    bad = context.get("quarantine", [])
    accepted = set(x for row in rows for x in sources(row))
    rejected = set(x for row in bad for x in sources(row))
    if accepted & rejected:
        raise DomainError("LINEAGE_OVERLAP", "同一来源行被重复裁决")
    m = {"accepted_inputs": len(accepted), "quarantine_inputs": len(rejected),
         "output_records": len(rows)}
    if intake:
        excluded = intake["duplicate"] + intake["blank"] + intake["repeated_headers"]
        m.update(input=intake["input"], excluded=excluded,
                 balanced=intake["input"] == len(accepted) + len(rejected) + excluded)
    else:
        m.update(input=len(accepted) + len(rejected), excluded=0, balanced=True)

    output = []
    pending = 0
    for row in rows:
        expected = Decimal(str(row.get("expected", "0")))
        reported = Decimal(str(row.get("reported", "0")))
        if row.get("path_decision") == TIME_PENDING:
            # 挂起件不进正式赔付：预期赔付强制为 0，且标记不可结算，
            # 重试/重算不会重复计赔。
            pending += 1
            status = "时效挂起"
            difference = Decimal(0) - reported
        else:
            status = "通过" if expected == reported else "差异"
            difference = expected - reported
        output.append({**row, "difference": str(difference), "status": status,
                       "settleable": row.get("path_decision") != TIME_PENDING})

    m.update(
        expected_total=str(sum((Decimal(str(r.get("expected", "0"))) for r in rows), Decimal(0))),
        reported_total=str(sum((Decimal(str(r.get("reported", "0"))) for r in rows), Decimal(0))),
        differences=sum(r["status"] == "差异" for r in output),
        time_pending=pending,
        settleable=sum(1 for r in output if r["settleable"]),
    )
    return {"rows": output, "quarantine": [], "metrics": m}
