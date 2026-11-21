"""夏令时（DST）本地时刻裁决核心。

背景：承运商晚到的补传记录只写当地时间（无 UTC 偏移）。在秋季回拨日
（fall-back），同一本地钟点会出现两次（fold=0 夏令时 / fold=1 标准时），
因此一个本地串对应两个真实 UTC 时刻，无法仅凭文本得到唯一时刻。

裁决原则（质控要求）：
- 拿不到唯一时刻的事件，不得进入正式时效；仅产出客服临时答复。
- 补传文件的先后顺序（received_time）只能排序，绝不能用于消歧——
  先到的文件可能对应更晚的真实签收，按顺序采纳会把超时误判为达标。
- 消歧只接受客观证据：承运商在补传中显式给出的 UTC 偏移，
  或同一包裹相邻扫描构成的路径单调性约束。
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.errors import DomainError

# 状态码
UNIQUE = "UNIQUE"            # 本地时刻唯一，可进正式时效
AMBIGUOUS = "AMBIGUOUS"      # 回拨日重复钟点，两个候选，无法定唯一
SKIPPED = "SKIPPED"          # 春季前跳不存在的钟点
INVALID = "INVALID"          # 无法解析

STATUS_LABEL = {
    UNIQUE: "唯一时刻",
    AMBIGUOUS: "回拨歧义",
    SKIPPED: "前跳空当",
    INVALID: "时间无效",
}


@dataclass
class LocalResolve:
    text: str
    zone: str
    status: str
    utc: str | None = None                       # 唯一 UTC（status=UNIQUE 时）
    candidates: list[dict] = field(default_factory=list)  # 全部候选时刻
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "local_time": self.text,
            "zone": self.zone,
            "time_status": self.status,
            "time_status_label": STATUS_LABEL.get(self.status, self.status),
            "event_utc": self.utc,
            "candidates": self.candidates,
            "time_reason": self.reason,
        }


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _candidate(naive: datetime, fold: int, tz: ZoneInfo) -> dict:
    aware = naive.replace(tzinfo=tz, fold=fold)
    off = aware.utcoffset()
    return {
        "fold": fold,
        "utc": _iso(aware),
        "offset_seconds": int(off.total_seconds()) if off is not None else None,
        "offset_label": _offset_label(off),
    }


def _offset_label(off) -> str:
    if off is None:
        return ""
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"UTC{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def resolve_local(text: str, zone: str) -> LocalResolve:
    """把无偏移的本地时间串按目的地时区解释为 UTC 候选。

    - 普通钟点：唯一 UTC。
    - 回拨重复钟点（fold 0/1 都有效且偏移不同）：两个候选，标记 AMBIGUOUS。
    - 春季前跳空当（fold 0/1 指向同一 UTC，本地钟点不存在）：标记 SKIPPED。
    """
    try:
        naive = datetime.fromisoformat(str(text))
        if naive.tzinfo is not None:
            # 带偏移的输入不应走本地裁决
            raise ValueError()
        tz = ZoneInfo(zone)
    except (TypeError, ValueError):
        return LocalResolve(text=str(text), zone=zone, status=INVALID,
                            reason="本地时间无法解析")

    first = _candidate(naive, 0, tz)
    second = _candidate(naive, 1, tz)
    if first["offset_seconds"] == second["offset_seconds"]:
        # 偏移相同 → 普通唯一钟点（gap 时 fold 0/1 偏移必不同）
        return LocalResolve(text=str(text), zone=zone, status=UNIQUE,
                            utc=first["utc"], candidates=[first],
                            reason="本地钟点唯一")

    # 偏移不同：可能是回拨（两个候选都映射回本钟点），
    # 也可能是前跳（两个候选都不映射回本钟点，该钟点不存在）。
    back_first = datetime.fromisoformat(first["utc"]).astimezone(tz).replace(tzinfo=None)
    back_second = datetime.fromisoformat(second["utc"]).astimezone(tz).replace(tzinfo=None)
    if back_first != naive and back_second != naive:
        return LocalResolve(text=str(text), zone=zone, status=SKIPPED,
                            candidates=[first, second],
                            reason="春季前跳，该本地钟点在该时区不存在")
    return LocalResolve(
        text=str(text), zone=zone, status=AMBIGUOUS,
        candidates=[first, second],
        reason="夏令时回拨，同一本地钟点对应两个真实时刻，缺乏消歧证据",
    )


def parse_offset_seconds(offset_text: str) -> int | None:
    """解析显式 UTC 偏移文本（如 '-05:00'、'+09:00'、'Z'）为秒。"""
    try:
        marker = datetime.fromisoformat("2000-01-01T00:00:00" + str(offset_text).replace("Z", "+00:00"))
        if marker.tzinfo is None:
            return None
        return int(marker.utcoffset().total_seconds())
    except (TypeError, ValueError):
        return None


def disambiguate(resolution: LocalResolve, offset_text: str | None,
                 evidence: str = "") -> LocalResolve:
    """用客观证据对歧义候选消歧。只接受显式 UTC 偏移。

    补传顺序（received_time）不在此列，严禁用于消歧。
    匹配成功返回新的 UNIQUE 决议；无证据或不匹配则原样返回（仍 AMBIGUOUS）。
    """
    if resolution.status != AMBIGUOUS or not offset_text:
        return resolution
    want = parse_offset_seconds(str(offset_text))
    if want is None:
        return resolution
    for cand in resolution.candidates:
        if cand["offset_seconds"] == want:
            chosen = dict(cand)
            label = _offset_label(timedelta(seconds=want))
            chosen["adopted_by"] = (evidence or "承运商显式偏移") + "：" + label
            return LocalResolve(
                text=resolution.text, zone=resolution.zone, status=UNIQUE,
                utc=cand["utc"], candidates=resolution.candidates,
                reason=f"经{chosen['adopted_by']}消歧，采纳 fold={cand['fold']}",
            )
    return resolution


def assert_aware_utc(value: str) -> datetime:
    """received_time 等必须带明确偏移，否则按 INVALID_TIME 隔离。"""
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise DomainError("INVALID_TIME", "时间需要明确偏移")
    return result.astimezone(timezone.utc)
