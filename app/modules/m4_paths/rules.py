from collections import defaultdict

from app.reasons import reject, sources

PRIORITY = {"ACCEPT": 0, "DEPART": 1, "CUSTOMS": 2, "DELIVERED": 3}

# 路径裁决结论
FORMAL = "FORMAL"          # 唯一时刻，可进正式时效
TIME_PENDING = "TIME_PENDING"  # 含回拨歧义，无唯一妥投时刻，仅供客服临时答复


def _sort_events(events):
    # 歧义事件 event_utc 为 None，排序时置于最后，单独由挂起分支处理。
    # 键保持同类型可比较：用 ISO 字符串排序，None 用哨兵排末尾。
    sentinel = "9999"
    return sorted(events, key=lambda r: (r["event_utc"] is None,
                                         r["event_utc"] if r["event_utc"] else sentinel,
                                         PRIORITY[r["node"]], r["scan_id"]))


def _validate(events, bad):
    """对一组（按某一候选时刻解释的）事件做同刻冲突与路径倒退校验。"""
    seen = {}
    valid = []
    last = -1
    for e in events:
        if e.get("event_utc") is None:
            continue  # 歧义事件不参与唯一时刻校验
        time = e["event_utc"]
        if time in seen and seen[time] != e["node"]:
            bad.append(reject(e, "SIMULTANEOUS_CONFLICT", "同刻扫描节点冲突"))
            continue
        seen[time] = e["node"]
        if PRIORITY[e["node"]] < last:
            bad.append(reject(e, "PATH_REGRESSION", "扫描路径倒退"))
            continue
        last = PRIORITY[e["node"]]
        valid.append(e)
    return valid


def evaluate(rows, context):
    groups = defaultdict(list)
    good = []
    bad = []
    for r in rows:
        groups[r["parcel"]].append(r)

    # 按包裹收集 M3 隔离但需要在临时答复中呈现的歧义事件（含两个候选时刻）
    ambiguous_by_parcel = defaultdict(list)
    for ev in context.get("ambiguous_events", []):
        ambiguous_by_parcel[ev.get("parcel")].append(ev)

    formal_count = pending_count = 0

    for parcel, events in sorted(groups.items()):
        events = _sort_events(events)
        valid = _validate(events, bad)

        accepts = [e for e in valid if e["node"] == "ACCEPT"]
        if not accepts:
            bad.extend(reject(e, "MISSING_ACCEPT", "缺少收件扫描") for e in valid)
            continue
        first = accepts[0]
        delivery = next((e for e in valid if e["node"] == "DELIVERED"), None)
        ambiguous_delivery = ambiguous_by_parcel.get(parcel, [])

        if ambiguous_delivery:
            # 妥投扫描落在回拨重复钟点：没有唯一时刻，正式时效挂起。
            # 仍重建两条候选路径，供客服当天答复；严禁按补传顺序任选其一。
            pending = _build_pending(parcel, first, valid,
                                     ambiguous_delivery, bad)
            if pending is not None:
                good.append(pending)
                pending_count += 1
            continue

        # 唯一妥投时刻（或暂未妥投）→ 正式路径
        record = _build_formal(first, valid, delivery)
        good.append(record)
        formal_count += 1

    return {
        "rows": good,
        "quarantine": bad,
        "metrics": {
            "parcels": len(good),
            "delivered": sum(r["delivered_at"] is not None for r in good),
            "formal_paths": formal_count,
            "time_pending": pending_count,
            "path_errors": len(bad),
        },
    }


def _build_formal(first, valid, delivery):
    source_lines = [line for e in valid for line in sources(e)]
    return {
        **first,
        "_sources": source_lines,
        "path_decision": FORMAL,
        "path_decision_label": "正式路径",
        "accepted_at": first["event_utc"],
        "delivered_at": delivery["event_utc"] if delivery else None,
        "delivery_uploaded_at": delivery["received_utc"] if delivery else None,
        "scan_count": len(valid),
        "path": " → ".join(e["node"] for e in valid),
        "reported": delivery["reported"] if delivery else first["reported"],
        "delivery_candidates": [],
        "time_reason": "妥投时刻唯一，纳入正式时效",
    }


def _build_pending(parcel, first, valid, ambiguous_events, bad):
    """对回拨歧义妥投重建候选路径。每个候选 UTC 都单独校验路径合法性。"""
    amb = next((e for e in ambiguous_events if e.get("node") == "DELIVERED"), None)
    if amb is None:
        return None
    candidates = []
    base_events = [e for e in valid if e["node"] != "DELIVERED"]
    for cand in amb["candidates"]:
        trial = dict(amb)
        trial["event_utc"] = cand["utc"]
        trial_events = _sort_events(base_events + [trial])
        local_bad = []
        checked = _validate(trial_events, local_bad)
        cand = dict(cand)
        if local_bad:
            cand["adopted"] = False
            cand["not_adopted_reason"] = "；".join(x["detail"] for x in local_bad)
        else:
            cand["adopted"] = False
            cand["not_adopted_reason"] = (
                "回拨歧义候选，无客观消歧证据，质控要求未获唯一时刻不进正式时效")
        cand["path"] = " → ".join(e["node"] for e in checked)
        candidates.append(cand)

    source_lines = [line for e in valid for line in sources(e)] + sources(amb)
    earliest = min(c["utc"] for c in candidates)
    latest = max(c["utc"] for c in candidates)
    return {
        **first,
        "_sources": source_lines,
        "path_decision": TIME_PENDING,
        "path_decision_label": "时效挂起·临时答复",
        "accepted_at": first["event_utc"],
        "delivered_at": None,  # 无唯一妥投时刻，正式字段保持为空
        "delivery_window_utc": {"earliest": earliest, "latest": latest},
        # 临时答复取最晚候选（最保守），避免把更晚签收误判为达标
        "provisional_delivered_at": latest,
        "delivery_uploaded_at": amb.get("received_utc"),
        "scan_count": len(valid) + 1,
        "path": " → ".join(e["node"] for e in base_events) + " → DELIVERED(歧义)",
        "reported": amb.get("reported", first["reported"]),
        "delivery_candidates": candidates,
        "ambiguous_local_time": amb.get("local_time"),
        "time_reason": amb.get("time_reason", "夏令时回拨，妥投时刻不唯一"),
    }
