"""夏令时回拨歧义裁决：唯一正式路径、候选时刻、临时答复、证据一致、幂等与回滚。"""
import csv
import io
import json

import pytest

from app.dst import resolve_local, disambiguate, parse_offset_seconds
from app.errors import DomainError
from app.pipeline import calculate, execute, load_sample, replace_run
from app import store
from app.modules.m4_paths.rules import TIME_PENDING, FORMAL

NY = "America/New_York"


# ---------- DST 引擎 ----------

def test_fallback_local_time_has_two_candidates():
    r = resolve_local("2026-11-01T01:30:00", NY)
    assert r.status == "AMBIGUOUS"
    utcs = [c["utc"] for c in r.candidates]
    assert utcs == ["2026-11-01T05:30:00+00:00", "2026-11-01T06:30:00+00:00"]
    assert {c["offset_label"] for c in r.candidates} == {"UTC-04:00", "UTC-05:00"}


def test_normal_local_time_is_unique():
    r = resolve_local("2026-11-01T12:30:00", NY)
    assert r.status == "UNIQUE" and r.utc == "2026-11-01T17:30:00+00:00"


def test_spring_forward_gap_is_skipped():
    r = resolve_local("2026-03-08T02:30:00", NY)
    assert r.status == "SKIPPED"
    assert "不存在" in r.reason


def test_explicit_offset_disambiguates_to_unique():
    r = resolve_local("2026-11-01T01:30:00", NY)
    d = disambiguate(r, "-05:00", "承运商补传偏移")
    assert d.status == "UNIQUE" and d.utc == "2026-11-01T06:30:00+00:00"
    assert "fold=1" in d.reason


def test_wrong_offset_does_not_disambiguate():
    r = resolve_local("2026-11-01T01:30:00", NY)
    d = disambiguate(r, "+09:00", "错误偏移")
    assert d.status == "AMBIGUOUS" and d.utc is None


def test_upload_order_is_never_a_disambiguator():
    # 无论补传先后，缺乏偏移/路径证据时结论都必须保持歧义
    r = resolve_local("2026-11-01T01:30:00", NY)
    assert disambiguate(r, None).status == "AMBIGUOUS"
    assert disambiguate(r, "").status == "AMBIGUOUS"


@pytest.mark.parametrize("text,want", [("-05:00", -18000), ("+09:00", 32400),
                                       ("Z", 0), ("garbage", None)])
def test_parse_offset(text, want):
    assert parse_offset_seconds(text) == want


# ---------- 端到端 DST 批次 ----------

@pytest.fixture
def dst_result():
    return calculate(load_sample("dst.csv"), {"as_of": "2026-11-18T00:00:00Z"})


def test_dst_batch_intake_flags_local(dst_result):
    assert dst_result["stages"][0]["metrics"]["local_time_batch"] is True


def test_ambiguous_delivery_becomes_time_pending(dst_result):
    rows = {r["parcel"]: r for r in dst_result["stages"][3]["rows"]}
    p = rows["1001"]
    assert p["path_decision"] == TIME_PENDING
    assert p["delivered_at"] is None  # 无唯一正式妥投时刻
    assert len(p["delivery_candidates"]) == 2
    assert all(c["adopted"] is False for c in p["delivery_candidates"])
    assert all("不进正式时效" in c["not_adopted_reason"] for c in p["delivery_candidates"])


def test_pending_uses_latest_candidate_for_provisional(dst_result):
    p = next(r for r in dst_result["stages"][3]["rows"] if r["path_decision"] == TIME_PENDING)
    assert p["provisional_delivered_at"] == "2026-11-01T06:30:00+00:00"  # 最晚候选
    assert p["delivery_window_utc"]["earliest"] == "2026-11-01T05:30:00+00:00"


def test_offset_evidence_makes_formal_path(dst_result):
    rows = {r["parcel"]: r for r in dst_result["stages"][3]["rows"]}
    p = rows["1002"]
    assert p["path_decision"] == FORMAL
    assert p["delivered_at"] == "2026-11-01T06:30:00+00:00"  # -05:00 消歧结果


def test_spring_gap_event_quarantined(dst_result):
    reasons = [q["reason"] for q in dst_result["stages"][2]["quarantine"]]
    assert "DST_SKIPPED_TIME" in reasons


def test_pending_parcel_excluded_from_formal_sla_and_comp(dst_result):
    m5 = dst_result["stages"][4]
    pending = next(r for r in m5["rows"] if r["parcel"] == "1001")
    assert pending["sla"] == "时效挂起" and pending["sla_binding"] == "临时"
    assert pending["expected"] == "0.00"
    assert m5["metrics"]["time_pending"] == 1
    m6 = dst_result["stages"][5]
    p6 = next(r for r in m6["rows"] if r["parcel"] == "1001")
    assert p6["settleable"] is False and p6["status"] == "时效挂起"
    assert all(r["expected"] != "0.00" or r["parcel"] != "1001"
               for r in m6["rows"] if r["settleable"])


def test_lineage_still_balanced_with_pending(dst_result):
    assert dst_result["stages"][5]["metrics"]["balanced"] is True
    assert dst_result["stages"][6]["metrics"]["time_pending_sealed"] == 1


# ---------- 非本地批次仍硬隔离无偏移时间 ----------

def test_naive_time_in_standard_batch_still_rejected(result):
    assert any(q["reason"] == "INVALID_TIME"
               for q in result["stages"][2]["quarantine"])


# ---------- 证据下载与页面结论一致 ----------

def test_archive_matches_screen_conclusion(dst_result):
    from app.archive import build_archive
    run = store.save(dst_result["run_id"], dst_result)
    text = build_archive(run)
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    header = rows[0]
    kind_idx = header.index("kind")
    binding_idx = header.index("binding")
    sla_idx = header.index("sla")
    evidence_idx = header.index("evidence")
    pending_line = next(r for r in rows[1:] if r[kind_idx] == "time_pending")
    assert pending_line[binding_idx] == "临时" and pending_line[sla_idx] == "时效挂起"
    payload = next(r for r in dst_result["stages"][6]["rows"]
                   if r["parcel"] == "1001")
    evidence = json.loads(pending_line[evidence_idx])
    # 证据 JSON 内含两个候选且与页面同源
    assert len(evidence["delivery_candidates"]) == 2
    assert evidence["provisional_delivered_at"] == payload["provisional_delivered_at"]
    assert evidence["sla"] == payload["sla"] == "时效挂起"


# ---------- 失败恢复旧版结论 + 原始扫描不误删 ----------

def test_failed_replace_keeps_previous_conclusion(client):
    first = client.post("/api/runs", json={"sample": "dst.csv"}).get_json()
    old_id = first["run_id"]
    # 新输入必然在计算阶段失败（坏表头），旧批次必须原样保留
    bad = client.post("/api/runs", json={"csv": "bad,header\n1,2"})
    assert bad.status_code in (422, 400)
    kept = store.get(old_id)
    assert kept["run_id"] == old_id
    # 原始扫描仍可只读取回
    raw = client.get(f"/api/runs/{old_id}/raw").get_json()
    assert raw["input_sha256"] == first["manifest"]["input_sha256"]
    assert len(raw["accepted_scans"]) == first["stages"][0]["metrics"]["accepted"]


def test_replace_run_does_not_delete_old_batch():
    old = execute(load_sample("dst.csv"))
    replace_run(old["run_id"], load_sample("clean.csv"))
    # 旧批次依旧可取（原始扫描未被删除）
    assert store.get(old["run_id"])["run_id"] == old["run_id"]


# ---------- 赔付幂等：重试不重复计赔 ----------

def test_compensation_claim_is_idempotent(client):
    p = client.post("/api/runs", json={"sample": "dst.csv"}).get_json()
    run_id = p["run_id"]
    settleable = next(r for r in p["stages"][5]["rows"] if r["settleable"])
    url = f"/api/runs/{run_id}/claims"
    first = client.post(url, json={"parcel": settleable["parcel"]})
    second = client.post(url, json={"parcel": settleable["parcel"]})
    assert first.get_json()["claimed"] is True
    again = second.get_json()
    assert again["claimed"] is False and again["already_claimed"] is True
    # 登记表里同包裹只有一条
    claims = store.list_claims(run_id)
    assert len([c for c in claims if c["parcel"] == settleable["parcel"]]) == 1


def test_pending_parcel_cannot_claim(client):
    p = client.post("/api/runs", json={"sample": "dst.csv"}).get_json()
    resp = client.post(f"/api/runs/{p['run_id']}/claims", json={"parcel": "1001"})
    assert resp.status_code == 409
    assert store.list_claims(p["run_id"]) == []


def test_idempotent_rerun_same_run_id(client):
    a = client.post("/api/runs", json={"sample": "dst.csv"}).get_json()
    b = client.post("/api/runs", json={"sample": "dst.csv"}).get_json()
    assert a["run_id"] == b["run_id"]
