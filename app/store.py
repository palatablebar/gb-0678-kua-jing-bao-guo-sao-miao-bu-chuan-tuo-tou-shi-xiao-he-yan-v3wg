import json
import sqlite3

from app.config import DATA_DIR
from app.errors import DomainError


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "records.db", timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,"
        "version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    # 赔付结算幂等表：同一 (批次, 包裹) 只允许结算一次，重试不会重复计赔。
    db.execute(
        "CREATE TABLE IF NOT EXISTS compensation_claims ("
        "run_id TEXT NOT NULL, parcel TEXT NOT NULL, amount TEXT NOT NULL, "
        "claimed_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "PRIMARY KEY (run_id, parcel)) WITHOUT ROWID")
    return db


def save(run_id, payload):
    # INSERT OR IGNORE：同源（含规则版本）重算只复用已有批次，绝不覆盖既有结论。
    # 若新计算在写库前抛错，execute() 不会到达这里，旧版结论原样保留。
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",
                   (run_id, json.dumps(payload, ensure_ascii=False)))
    return get(run_id)


def get(run_id):
    with connect() as db:
        row = db.execute("SELECT payload,version,decision FROM runs WHERE id=?",
                         (run_id,)).fetchone()
    if not row:
        raise DomainError("NOT_FOUND", "批次不存在", 404)
    return {**json.loads(row[0]), "version": row[1], "decision": row[2]}


def latest():
    with connect() as db:
        row = db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None


def history():
    with connect() as db:
        return [{"run_id": i, "version": v, "decision": d}
                for i, v, d in db.execute(
                    "SELECT id,version,decision FROM runs ORDER BY rowid DESC")]


def review(run_id, version, decision):
    if decision not in {"已复核", "退回复核"}:
        raise DomainError("BAD_DECISION", "复核状态无效")
    with connect() as db:
        cursor = db.execute(
            "UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",
            (decision, run_id, version))
        if cursor.rowcount != 1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                raise DomainError("NOT_FOUND", "批次不存在", 404)
            raise DomainError("STALE_VERSION", "版本已更新，请刷新", 409)
    return get(run_id)


def claim_compensation(run_id, parcel, amount):
    """幂等赔付登记。首次返回 True；同一 (批次,包裹) 重试返回 False，不重复计赔。"""
    with connect() as db:
        cursor = db.execute(
            "INSERT OR IGNORE INTO compensation_claims(run_id,parcel,amount) "
            "VALUES (?,?,?)", (run_id, str(parcel), str(amount)))
        return cursor.rowcount == 1


def list_claims(run_id):
    with connect() as db:
        return [{"parcel": p, "amount": a} for p, a in db.execute(
            "SELECT parcel,amount FROM compensation_claims WHERE run_id=? ORDER BY parcel",
            (run_id,))]
