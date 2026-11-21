from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from decimal import Decimal

from app.values import instant, number, money
from app.errors import DomainError
from app.modules.m4_paths.rules import TIME_PENDING

HOLIDAYS = {"JP": {"2026-11-03", "2026-11-23"}, "US": {"2026-11-26"},
            "DE": {"2026-10-03"}}

SLA_FORMAL_OK = "达标"
SLA_FORMAL_LATE = "超时"
SLA_PENDING = "时效挂起"          # 无唯一时刻，不进正式时效
PROVISIONAL_LATE = "临时判定·倾向超时"
PROVISIONAL_OK = "临时答复·倾向达标（待证据）"


def working_hours(start, end, zone, holidays):
    if end < start:
        raise DomainError("NEGATIVE_INTERVAL", "妥投早于收件")
    tz = ZoneInfo(zone)
    cursor = start
    seconds = Decimal(0)
    while cursor < end:
        local = cursor.astimezone(tz)
        next_day = datetime.combine(local.date() + timedelta(days=1), time.min,
                                    tzinfo=tz).astimezone(cursor.tzinfo)
        stop = min(end, next_day)
        if local.weekday() < 5 and local.date().isoformat() not in holidays:
            seconds += Decimal(str((stop - cursor).total_seconds()))
        cursor = stop
    return seconds / 3600


def evaluate(rows, context):
    p = context.get("parameters", {})
    as_of = instant(p.get("as_of", "2026-11-18T00:00:00Z"))
    rate = number(p.get("hourly_compensation", "1.00"))
    if rate < 0:
        raise DomainError("BAD_RATE", "赔付费率不能为负")

    good = []
    formal_late = pending = 0
    formal_comp = Decimal(0)

    for row in rows:
        start = instant(row["accepted_at"])
        holidays = set(p.get("holidays", {}).get(row["destination"],
                                                 HOLIDAYS.get(row["destination"], set())))

        if row.get("path_decision") == TIME_PENDING:
            # 无唯一妥投时刻 → 正式时效挂起，不产生赔付。
            # 客服临时答复取最晚候选：只要最晚时刻已超时，则无论真实是哪一次，
            # 都必然超时（最保守，避免误判达标）。
            latest = instant(row["provisional_delivered_at"])
            prov_hours = working_hours(start, latest, row["zone"], holidays)
            prov_late = max(Decimal(0), prov_hours - number(row["promised_hours"]))
            pending += 1
            good.append({
                **row,
                "sla": SLA_PENDING,
                "sla_binding": "临时",
                "working_hours": str(prov_hours),
                "late_hours": str(prov_late),
                "expected": money(Decimal(0)),   # 挂起期间不计赔
                "clock_end": latest.isoformat(),
                "provisional_sla": PROVISIONAL_LATE if prov_late > 0 else PROVISIONAL_OK,
                "provisional_late_hours": str(prov_late),
                "formal_note": "未获唯一妥投时刻，不进正式时效、不计赔付；待承运商补偏移证据后重算",
            })
            continue

        end = instant(row["delivered_at"]) if row.get("delivered_at") else max(start, as_of)
        hours = working_hours(start, end, row["zone"], holidays)
        late = max(Decimal(0), hours - number(row["promised_hours"]))
        comp = min(number(row["declared_value"]) * Decimal("0.10"), late * rate)
        if late > 0:
            formal_late += 1
        formal_comp += comp
        good.append({
            **row,
            "sla_binding": "正式",
            "working_hours": str(hours),
            "late_hours": str(late),
            "expected": money(comp),
            "sla": SLA_FORMAL_LATE if late > 0 else SLA_FORMAL_OK,
            "clock_end": end.isoformat(),
        })

    return {
        "rows": good,
        "quarantine": [],
        "metrics": {
            "parcels": len(good),
            "formal_parcels": len(good) - pending,
            "late_parcels": formal_late,
            "time_pending": pending,
            "compensation": money(formal_comp),
        },
    }
