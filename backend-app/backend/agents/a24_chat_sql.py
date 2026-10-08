"""Agent 24 - Chat (NL -> SQL). The SQL policy gate is deterministic code; the model only proposes.

Pipeline: (1) prompt contains ONLY the schema the caller may see (agent 23 visible columns; locked columns omitted);
(2) llm_guard produces {sql} (strict JSON; ungrounded output is dropped -> clarification); (3) sqlglot (sqlite dialect) parses;
(4) policy gate (`gate`): exactly ONE statement that is a SELECT (CTEs allowed); no comments; no DDL/DML/PRAGMA/ATTACH/Command
nodes anywhere in the tree (subqueries, CTEs included); only known resources (CTE/derived aliases tracked); no sqlite_*/system
tables; function denylist (config.chat.function_denylist); every column resolved to a table and must be visible to the caller;
`SELECT *` / `t.*` allowed only when every column of the table is visible; unknown or locked columns -> BLOCK.
(5) analysis from the AST (operation, tables, columns, where, row_estimate = largest referenced table row count - an upper
bound; sqlite has no EXPLAIN row estimates); (6) decision: BLOCK (reason, nothing runs) / CONFIRM (sensitive columns referenced or
row estimate > config.chat.confirm_row_estimate before LIMIT; approval_id created, nothing runs) / ALLOW.
Execution only on ALLOW: LIMIT injected/capped to config.chat.row_cap; the query runs on a throw-away in-memory SQLite that holds
ONLY visible columns of the caller's rows (locked columns never loaded), with query_only=ON and a progress-handler timeout.
answer_text comes from llm_guard given the returned rows as untrusted context; any number not present in the rows is rejected and a
deterministic summary is used instead. Conversation memory = previous questions only. Every attempt is audited; CONFIRM and repeated
BLOCKs notify the admin.
"""
import hashlib
import re
import sqlite3
import time
import uuid
from typing import Optional

import sqlglot
from sqlglot import exp
from pydantic import BaseModel, ConfigDict

from ..common import analytics, audit, auth, config, llm_guard, notify
from ..common.errors import AgentError, ErrorCode
from ..common.store import store
from . import a23_access_control as access

FORBIDDEN_NODES = tuple(n for n in (getattr(exp, x, None) for x in (
    "Insert", "Update", "Delete", "Create", "Drop", "Alter", "Command", "Pragma", "Attach", "Detach", "Merge", "Copy",
    "TruncateTable", "Transaction", "Commit", "Rollback", "Set", "Use", "AlterTable", "Grant")) if n is not None)


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    conversation_id: Optional[str] = None
    scope: Optional[dict] = None


class ChatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    sql: Optional[str] = None
    analysis: Optional[dict] = None
    decision: str
    reason: Optional[str] = None
    approval_id: Optional[str] = None
    columns: Optional[list[str]] = None
    rows: Optional[list[list]] = None
    answer_text: Optional[str] = None
    citations: Optional[list[dict]] = None
    error: Optional[str] = None


class _SqlOut(BaseModel):
    sql: Optional[str] = None
    clarification: Optional[str] = None


class _AnswerOut(BaseModel):
    answer_text: Optional[str] = None


class Block(Exception):
    pass


def _visible_map(u: auth.User) -> dict[str, list[str]]:
    return {r: access.visible_columns(u, r) for r in analytics.RESOURCES}


def gate(sql: str, visible: dict[str, list[str]]) -> tuple[exp.Expression, dict]:
    """Raises Block(reason) on any violation. Returns (ast, analysis)."""
    if "--" in sql or "/*" in sql or "*/" in sql:
        raise Block("SQL comments are not allowed")
    try:
        stmts = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError:
        raise Block("SQL could not be parsed")
    if len(stmts) != 1:
        raise Block("Exactly one statement is allowed")
    tree = stmts[0]
    if not isinstance(tree, exp.Select):
        raise Block("Only SELECT statements are allowed")
    if tree.find(*FORBIDDEN_NODES):
        raise Block("Statement contains a forbidden operation")
    deny = {f.lower() for f in config.get("chat.function_denylist", [])}
    for f in tree.find_all(exp.Func):
        name = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if name in deny or name.startswith("pragma_") or name.startswith("sqlite_"):
            raise Block(f"Function not allowed: {name}")
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    derived = {s.alias.lower() for s in tree.find_all(exp.Subquery) if s.alias}
    alias_to_table: dict[str, str] = {}
    tables: list[str] = []
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if t.args.get("db") or t.args.get("catalog"):
            raise Block("Qualified schema/database names are not allowed")
        if name in cte_names:
            continue
        if name.startswith("sqlite_") or name.startswith("pragma"):
            raise Block("System tables are not allowed")
        if name not in analytics.RESOURCES:
            raise Block(f"Unknown or unavailable table: {name}")
        tables.append(name)
        alias_to_table[(t.alias or name).lower()] = name
        alias_to_table[name] = name
    if not tables:
        raise Block("Query must reference at least one table")
    proj_alias = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
    refs: set[tuple[str, str]] = set()
    for star in tree.find_all(exp.Star):
        parent = star.parent
        if isinstance(parent, exp.Count):
            continue
        qual = parent.table.lower() if isinstance(parent, exp.Column) and parent.table else None
        targets = [alias_to_table[qual]] if qual in alias_to_table else (set(tables) if not qual else [])
        for tb in targets:
            for c in analytics.RESOURCES[tb]:
                if c not in visible[tb]:
                    raise Block("Query uses * on a table with locked columns")
                refs.add((tb, c))
    for col in tree.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue
        cname, qual = col.name.lower(), (col.table or "").lower()
        if qual and (qual in cte_names or qual in derived) and qual not in alias_to_table:
            continue
        if qual:
            tb = alias_to_table.get(qual)
            if tb is None:
                raise Block(f"Unknown table reference: {qual}")
            cands = [tb]
        else:
            cands = [t for t in set(tables) if cname in analytics.RESOURCES[t]]
            if not cands:
                if cname in proj_alias or cte_names or derived:
                    continue
                raise Block(f"Unknown column: {cname}")
        hit = False
        for tb in cands:
            if cname in analytics.RESOURCES[tb]:
                if cname not in visible[tb]:
                    raise Block(f"Column is locked for this user: {tb}.{cname}")
                refs.add((tb, cname))
                hit = True
        if not hit:
            if cname in proj_alias or cte_names or derived:
                continue
            raise Block(f"Unknown column: {cname}")
    for tb in set(tables):
        if not visible[tb]:
            raise Block(f"No visible columns on {tb}")
    where = tree.args.get("where")
    analysis = {"operation": "SELECT", "tables": sorted(set(tables)), "columns": sorted(f"{t}.{c}" for t, c in refs),
                "where": where.this.sql(dialect="sqlite") if where else None, "row_estimate": None,
                "sensitive_columns": sorted(f"{t}.{c}" for t, c in refs if analytics.RESOURCES[t][c])}
    return tree, analysis


def _limit(tree: exp.Select, cap: int) -> tuple[exp.Select, bool]:
    lim = tree.args.get("limit")
    if lim is not None:
        try:
            if int(lim.expression.name) <= cap:
                return tree, True
        except (ValueError, AttributeError):
            pass
    return tree.limit(cap), False


def _build_db(u: auth.User, visible: dict[str, list[str]]) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    for res, cols in visible.items():
        if not cols:
            continue
        db.execute(f"CREATE TABLE {res} ({', '.join(cols)})")
        rows = analytics.load_rows(res, u)
        db.executemany(f"INSERT INTO {res} VALUES ({','.join('?' * len(cols))})", [[r.get(c) for c in cols] for r in rows])
    db.execute("PRAGMA query_only = ON")
    return db


def _execute(db: sqlite3.Connection, sql: str) -> tuple[list[str], list[list]]:
    deadline = time.monotonic() + config.get("chat.timeout_ms", 5000) / 1000
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        cur = db.execute(sql)
        cols = [d[0] for d in cur.description]
        return cols, [list(r) for r in cur.fetchall()]
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise AgentError(ErrorCode.TIMEOUT, "Query timed out")
        raise AgentError(ErrorCode.INVALID_INPUT, "Query could not be executed")


def _citations(cols: list[str], rows: list[list], u: auth.User) -> list[dict]:
    out = []
    for ri, r in enumerate(rows):
        d = dict(zip(cols, r))
        if "block_id" in d and d["block_id"]:
            out.append({"row": ri, "block_id": d["block_id"], "source_id": d.get("source_id")})
        elif "fact_id" in d and d["fact_id"] and d.get("case_id"):
            f = next((x for x in (store.get("facts", d["case_id"]) or {"facts": []})["facts"] if x["fact_id"] == d["fact_id"]), None)
            if f:
                out.append({"row": ri, "block_id": f["evidence"][0]["block_id"], "source_id": f["source_id"]})
    return out


def run(inp: ChatInput) -> ChatOutput:
    u = auth.require("chat")
    q = inp.question.strip()
    if not q:
        raise AgentError(ErrorCode.INVALID_INPUT, "Question is empty")
    cid = inp.conversation_id or uuid.uuid4().hex
    conv = store.get("conversation", cid, {"user": u.user_id, "questions": []})
    if conv["user"] != u.user_id:
        raise AgentError(ErrorCode.FORBIDDEN, "Conversation not accessible")
    prior = conv["questions"][-config.get("chat.memory_questions", 5):]
    conv["questions"].append(q)
    store.put("conversation", cid, conv)
    qid = hashlib.sha256((cid + q).encode()).hexdigest()[:16]
    audit.append(event_type="question_asked", object_type="conversation", object_id=cid, details={"question_id": qid, "len": len(q)})

    def blocked(reason: str, sql: Optional[str] = None) -> ChatOutput:
        key = f"blocks:{u.user_id}"
        n = (store.get("counter", key) or 0) + 1
        store.put("counter", key, n)
        audit.append(event_type="sql_decision", object_type="conversation", object_id=cid, outcome="denied",
                     details={"decision": "BLOCK", "reason": reason, "sql_hash": hashlib.sha256((sql or "").encode()).hexdigest()[:16]})
        if n >= config.get("chat.repeat_block_threshold", 3):
            notify.send("chat_repeated_block", "high", "Repeated blocked chat queries", f"user={u.user_id} role={u.role} count={n}",
                        dedupe_key=f"chat_block:{u.user_id}")
        return ChatOutput(conversation_id=cid, sql=sql, decision="BLOCK", reason=reason)

    visible = _visible_map(u)
    schema_txt = "\n".join(f"TABLE {r}({', '.join(c)})" for r, c in visible.items() if c)
    ctx = [{"block_id": "schema", "text": schema_txt}, {"block_id": "question", "text": q}] + \
          [{"block_id": f"prior{i}", "text": p} for i, p in enumerate(prior)]
    g = llm_guard.call("Write ONE read-only SQLite SELECT answering the question using only the listed tables/columns. "
                       "If the question is ambiguous or cannot be answered from the schema, set clarification instead of sql.",
                       ctx, _SqlOut)
    if not g["ok"] or not g["output"]:
        return ChatOutput(conversation_id=cid, decision="BLOCK", reason="SQL could not be generated",
                          answer_text="I could not produce a safe query for that question; please rephrase.")
    o = g["output"]
    if not o.get("sql"):
        return ChatOutput(conversation_id=cid, decision="BLOCK", reason="Clarification needed",
                          answer_text=o.get("clarification") or "Could you clarify the question?")
    sql = o["sql"].strip().rstrip(";")
    try:
        tree, analysis = gate(sql, visible)
    except Block as b:
        return blocked(str(b), sql)
    cap = config.get("chat.row_cap", 500)
    tree, had_limit = _limit(tree, cap)
    final_sql = tree.sql(dialect="sqlite")
    est = max((len(analytics.load_rows(t, u)) for t in analysis["tables"]), default=0)
    analysis["row_estimate"] = est
    sensitive = bool(analysis["sensitive_columns"])
    large = est > config.get("chat.confirm_row_estimate", 1000) and not had_limit
    if sensitive or large:
        aid = uuid.uuid4().hex
        store.put("chat_approval", aid, {"approval_id": aid, "user_id": u.user_id, "role": u.role, "sql": final_sql, "status": "pending",
                                         "reason": "sensitive columns" if sensitive else "large scan"})
        audit.append(event_type="sql_decision", object_type="conversation", object_id=cid,
                     details={"decision": "CONFIRM", "approval_id": aid, "sensitive": analysis["sensitive_columns"], "estimate": est})
        notify.send("chat_confirm_pending", "high", "Chat query awaiting approval", f"approval={aid} user={u.user_id} role={u.role}",
                    link=f"/admin/chat/{aid}")
        return ChatOutput(conversation_id=cid, sql=final_sql, analysis=analysis, decision="CONFIRM", approval_id=aid,
                          reason="Query touches sensitive columns" if sensitive else "Query scans a large number of rows")
    audit.append(event_type="sql_decision", object_type="conversation", object_id=cid,
                 details={"decision": "ALLOW", "tables": analysis["tables"], "sql_hash": hashlib.sha256(final_sql.encode()).hexdigest()[:16]})
    db = _build_db(u, visible)
    try:
        cols, rows = _execute(db, final_sql)
    finally:
        db.close()
    audit.append(event_type="query_executed", object_type="conversation", object_id=cid, details={"rows": len(rows), "columns": cols})
    summary = f"{len(rows)} row(s) returned."
    answer = summary
    if rows:
        rows_txt = [{"block_id": f"row{i}", "text": ", ".join(f"{c}={v}" for c, v in zip(cols, r))} for i, r in enumerate(rows[:50])]
        a = llm_guard.call("Summarise the result rows in one or two neutral sentences. Do not add numbers that are not in the rows.",
                           rows_txt + [{"block_id": "count", "text": f"row_count={len(rows)}"}], _AnswerOut, policy="reject_all")
        if a["ok"] and a["output"] and a["output"].get("answer_text"):
            answer = a["output"]["answer_text"]
    return ChatOutput(conversation_id=cid, sql=final_sql, analysis=analysis, decision="ALLOW", columns=cols, rows=rows,
                      answer_text=answer, citations=_citations(cols, rows, u))


class ApproveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str
    decision: str  # "approve" | "reject"


def approve(inp: ApproveInput) -> ChatOutput:
    """Admin decision on a CONFIRM query. Approval re-runs the gate under the REQUESTER's visibility, never the admin's."""
    admin = auth.require("admin")
    rec = store.get("chat_approval", inp.approval_id)
    if rec is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Approval not found")
    if rec["status"] != "pending":
        raise AgentError(ErrorCode.CONFLICT, "Approval already decided")
    if rec["user_id"] == admin.user_id:
        raise AgentError(ErrorCode.FORBIDDEN, "Approvers cannot approve their own queries")
    if inp.decision not in ("approve", "reject"):
        raise AgentError(ErrorCode.INVALID_INPUT, "decision must be approve or reject")
    rec["status"] = "approved" if inp.decision == "approve" else "rejected"
    store.put("chat_approval", inp.approval_id, rec)
    audit.append(event_type="sql_decision", object_type="chat_approval", object_id=inp.approval_id,
                 details={"decision": inp.decision, "requester": rec["user_id"]})
    if inp.decision == "reject":
        return ChatOutput(conversation_id="", sql=rec["sql"], decision="BLOCK", reason="Rejected by approver")
    requester = auth.make_user(rec["user_id"], rec["role"])
    token = auth.set_user(requester)
    try:
        visible = _visible_map(requester)
        try:
            tree, analysis = gate(rec["sql"], visible)
        except Block as b:
            return ChatOutput(conversation_id="", sql=rec["sql"], decision="BLOCK", reason=str(b))
        db = _build_db(requester, visible)
        try:
            cols, rows = _execute(db, tree.sql(dialect="sqlite"))
        finally:
            db.close()
    finally:
        auth._current.reset(token)
    audit.append(event_type="query_executed", object_type="chat_approval", object_id=inp.approval_id,
                 details={"rows": len(rows), "columns": cols, "approved_by": admin.user_id})
    return ChatOutput(conversation_id="", sql=rec["sql"], analysis=analysis, decision="ALLOW", columns=cols, rows=rows,
                      answer_text=f"{len(rows)} row(s) returned.", citations=_citations(cols, rows, requester))
