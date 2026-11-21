from datetime import datetime

from app.values import instant, number
from app.reasons import reject
from app.errors import DomainError
from app import dst


def _is_naive(text: str) -> bool:
    try:
        parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is None


def evaluate(rows, context):
    good = []
    bad = []
    ambiguous_events = []
    ambiguous = skipped = local_unique = 0
    for row in rows:
        try:
            received = instant(row["received_time"])
            promised = number(row["promised_hours"])
            value = number(row["declared_value"])
            reported = number(row["reported"])
            if promised < 0 or value < 0 or reported < 0:
                raise DomainError("NEGATIVE_VALUE", "时限与金额不能为负")

            if _is_naive(row["event_time"]):
                if not row.get("_local_batch"):
                    # 非本地补传批：无偏移时间仍然硬隔离（保持输入纪律）。
                    raise DomainError("INVALID_TIME", "时间需要明确偏移")
                # 承运商补传只给当地时间：按目的地时区裁决，绝不按补传顺序猜。
                resolution = dst.resolve_local(row["event_time"], row["zone"])
                if resolution.status == dst.AMBIGUOUS:
                    resolution = dst.disambiguate(
                        resolution, row.get("clock_offset"), "承运商补传显式偏移")
                if resolution.status == dst.AMBIGUOUS:
                    # 不进隔离（避免与候选路径来源行冲突），而是带歧义标志继续流动，
                    # 由 M4 生成时效挂起的临时路径，M5 不纳入正式时效。
                    ambiguous += 1
                    ambiguous_events.append({**row, **resolution.to_dict()})
                    good.append({
                        **row, **resolution.to_dict(),
                        "event_utc": None,
                        "received_utc": received.isoformat(),
                        "upload_lag_hours": "0",
                        "promised_hours": str(promised),
                        "declared_value": str(value),
                        "reported": str(reported),
                    })
                    continue
                if resolution.status == dst.SKIPPED:
                    skipped += 1
                    bad.append(reject(row, "DST_SKIPPED_TIME",
                                      "春季前跳空当，该本地钟点不存在"))
                    continue
                if resolution.status == dst.INVALID:
                    bad.append(reject(row, "INVALID_TIME", "本地时间无法解析"))
                    continue
                local_unique += 1
                event = instant(resolution.utc)
                time_note = resolution.to_dict()
            else:
                event = instant(row["event_time"])
                time_note = {"time_status": dst.UNIQUE,
                             "time_status_label": dst.STATUS_LABEL[dst.UNIQUE],
                             "candidates": [], "time_reason": "输入带明确偏移",
                             "local_time": row["event_time"]}

            if received < event:
                raise DomainError("RECEIVED_BEFORE_EVENT", "上传早于事件时间")
            good.append({
                **row,
                **time_note,
                "event_utc": event.isoformat(),
                "received_utc": received.isoformat(),
                "upload_lag_hours": str(number(str((received - event).total_seconds())) / 3600),
                "promised_hours": str(promised),
                "declared_value": str(value),
                "reported": str(reported),
            })
        except (DomainError, KeyError) as e:
            bad.append(reject(row, getattr(e, "code", "MISSING_FIELD"), str(e)))
    context["ambiguous_events"] = ambiguous_events
    return {
        "rows": good,
        "quarantine": bad,
        "ambiguous_events": ambiguous_events,
        "metrics": {
            "accepted": len(good),
            "late_uploads": sum(number(r["upload_lag_hours"]) > 24 for r in good),
            "local_unique": local_unique,
            "dst_ambiguous": ambiguous,
            "dst_skipped": skipped,
        },
    }
