"""Per-trip execution spans and short-lived reuse of sanitized query results."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlparse
from uuid import uuid4


REUSE_SECONDS = 600
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ExecutionError(ValueError):
    pass


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("执行时间必须包含时区")
    return parsed


def identifier(value: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ExecutionError("执行阶段、操作和记录 ID 必须是小写字母、数字、短横线或下划线，最多64字符")
    return value


def workspace_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ExecutionError("执行记录需要有效的旅行研究工作区") from error
    if not isinstance(manifest, dict) or manifest.get("workspace_version") != 1:
        raise ExecutionError("执行记录不支持此工作区版本")
    return root


def infer_workspace(path: Path) -> Path | None:
    for parent in path.expanduser().resolve().parents:
        if (parent / "manifest.json").is_file():
            try:
                return workspace_root(parent)
            except ExecutionError:
                continue
    return None


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".execution-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def begin(root: Path, phase: str, operation: str, category: str = "phase", task_id: str | None = None) -> dict[str, Any]:
    entry = {
        "schema_version": "travel-execution-span/v1", "id": uuid4().hex,
        "phase": identifier(phase), "operation": identifier(operation),
        "category": category, "status": "running", "started_at": stamp(),
    }
    if task_id:
        entry["task_id"] = identifier(task_id)
    write_json(root / "state/execution/spans" / f"{entry['id']}.json", entry)
    return entry


def finish(root: Path, span_id: str, status: str = "success", elapsed_ms: int | None = None,
           metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    path = root / "state/execution/spans" / f"{identifier(span_id)}.json"
    entry = read_json(path)
    if not isinstance(entry, dict) or entry.get("status") != "running":
        raise ExecutionError("执行记录不存在或已结束；不会覆盖历史计时")
    if status not in {"success", "failed", "cancelled", "no_results"}:
        raise ExecutionError("执行记录的结束状态无效")
    ended = stamp()
    entry.update(status=status, ended_at=ended, elapsed_ms=max(0, elapsed_ms if elapsed_ms is not None else
                 round((parse_stamp(ended) - parse_stamp(entry["started_at"])).total_seconds() * 1000)))
    # Only internal counters/IDs are accepted; no prompt, keyword, URL or error body.
    for key in ("mode", "query_key", "error_type"):
        if metadata and key in metadata:
            entry[key] = metadata[key]
    write_json(path, entry)
    return entry


@contextmanager
def measured(workspace: str | Path | None, phase: str, operation: str, category: str = "command",
             task_id: str | None = None) -> Iterator[dict[str, Any]]:
    if not workspace:
        yield {}
        return
    root = workspace_root(workspace)
    entry = begin(root, phase, operation, category, task_id)
    started = time.monotonic()
    metadata: dict[str, Any] = {}
    try:
        yield metadata
    except BaseException as error:
        metadata["error_type"] = type(error).__name__
        finish(root, entry["id"], "failed", round((time.monotonic() - started) * 1000), metadata)
        raise
    else:
        finish(root, entry["id"], metadata.get("status", "success"), round((time.monotonic() - started) * 1000), metadata)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def unsafe_payload(value: Any) -> bool:
    forbidden = {"key", "apikey", "cookie", "cookies", "authorization", "password", "token",
                 "xsectoken", "accesstoken", "refreshtoken", "signature", "sign", "secret", "credential",
                 "xamzsignature", "xamzcredential", "xamzsecuritytoken", "xosssignature", "ossaccesskeyid"}
    if isinstance(value, dict):
        return any(str(key).casefold().replace("_", "").replace("-", "") in forbidden
                   or unsafe_payload(item) for key, item in value.items())
    if isinstance(value, list):
        return any(unsafe_payload(item) for item in value)
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        try:
            parsed = urlparse(value)
            return bool(parsed.username or parsed.password) or any(
                key.casefold().replace("_", "").replace("-", "") in forbidden for key, _ in parse_qsl(parsed.query)
            )
        except ValueError:
            return True
    return False


def empty_result(payload: dict[str, Any]) -> bool:
    return payload.get("status") == "no_results" or payload.get("count") == 0 or any(
        key in payload and not payload[key] for key in ("places", "notes", "results", "options", "forecasts")
    )


def expiry(payload: dict[str, Any]) -> str | None:
    if payload.get("status") not in {"platform_reported", "community_reported", "authenticated", "ready"}:
        return None
    if empty_result(payload):
        return None
    snapshots = payload.get("snapshots") or [payload]
    deadlines = []
    try:
        for item in snapshots:
            if not isinstance(item, dict):
                return None
            if item.get("status") == "no_results":
                return None
            freshness = item.get("freshness") or {}
            if not isinstance(freshness, dict):
                return None
            checked = parse_stamp(freshness.get("checked_at") or item["checked_at"])
            if checked > datetime.now(timezone.utc):
                return None
            deadlines.append(checked + timedelta(seconds=REUSE_SECONDS))
            if freshness.get("expires_at"):
                deadlines.append(parse_stamp(freshness["expires_at"]))
    except (ValueError, KeyError, TypeError):
        return None
    return min(deadlines).isoformat()


@contextmanager
def query_lock(path: Path, timeout: float = 180) -> Iterator[None]:
    """Serialize equal queries across processes without serializing other queries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            def acquire():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        started = time.monotonic()
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() - started >= timeout:
                    raise ExecutionError("相同查询仍在执行；未重复请求上游，请稍后复用结果") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            release()


def cached_result(root: Path, key: str) -> dict[str, Any] | None:
    record = read_json(root / "state/execution/cache" / f"{key}.json")
    if not isinstance(record, dict) or record.get("key") != key:
        return None
    payload = record.get("payload")
    try:
        deadline = expiry(payload) if isinstance(payload, dict) else None
        if (not isinstance(payload, dict) or unsafe_payload(payload)
                or deadline is None
                or fingerprint(payload) != record.get("payload_sha256")
                or min(parse_stamp(record["expires_at"]), parse_stamp(deadline)) <= datetime.now(timezone.utc)):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return record


def store_result(root: Path, key: str, payload: dict[str, Any]) -> None:
    deadline = expiry(payload)
    if not deadline or unsafe_payload(payload) or parse_stamp(deadline) <= datetime.now(timezone.utc):
        return
    write_json(root / "state/execution/cache" / f"{key}.json", {
        "key": key, "payload": payload, "payload_sha256": fingerprint(payload), "expires_at": deadline,
    })


def union_ms(intervals: list[tuple[float, float]]) -> int:
    total, end = 0.0, float("-inf")
    for start, stop in sorted(intervals):
        total += max(0.0, stop - max(start, end))
        end = max(end, stop)
    return round(total * 1000)


def report(root: Path) -> dict[str, Any]:
    entries = [read_json(path) for path in sorted((root / "state/execution/spans").glob("*.json"))]
    entries = [entry for entry in entries if isinstance(entry, dict)]
    phases: dict[str, dict[str, Any]] = {}
    open_ids, measured_intervals, waiting_intervals = [], [], []
    current = datetime.now(timezone.utc).timestamp()
    for entry in entries:
        try:
            start = parse_stamp(entry["started_at"]).timestamp()
            end = parse_stamp(entry["ended_at"]).timestamp() if entry.get("ended_at") else current
            phase = phases.setdefault(entry["phase"], {"phase": entry["phase"], "intervals": [],
                "command_intervals": [], "query_intervals": [], "command_work_ms": 0, "query_work_ms": 0,
                "fresh_queries": 0, "reused_queries": 0, "failed_queries": 0, "empty_queries": 0})
        except (KeyError, TypeError, ValueError):
            continue
        if entry.get("status") == "running":
            open_ids.append(entry["id"])
        if entry.get("category") == "phase":
            interval = (start, max(start, end))
            phase["intervals"].append(interval)
            if entry["phase"] != "waiting_for_user":
                measured_intervals.append(interval)
            else:
                waiting_intervals.append(interval)
        elif entry.get("category") == "command":
            phase["command_work_ms"] += entry.get("elapsed_ms", 0)
            phase["command_intervals"].append((start, max(start, end)))
        elif entry.get("category") == "query":
            phase["query_work_ms"] += entry.get("elapsed_ms", 0)
            phase["query_intervals"].append((start, max(start, end)))
            phase["fresh_queries"] += entry.get("mode") == "fresh"
            phase["reused_queries"] += entry.get("mode") == "reused"
            phase["failed_queries"] += entry.get("status") == "failed"
            phase["empty_queries"] += entry.get("status") == "no_results"
    for phase in phases.values():
        phase["phase_elapsed_ms"] = union_ms(phase.pop("intervals"))
        phase["command_elapsed_ms"] = union_ms(phase.pop("command_intervals"))
        phase["query_elapsed_ms"] = union_ms(phase.pop("query_intervals"))
    slow = [{key: entry[key] for key in ("phase", "operation", "category", "elapsed_ms", "status", "mode") if key in entry}
            for entry in entries if entry.get("category") in {"command", "query"} and "elapsed_ms" in entry]
    return {"schema_version": "travel-execution-report/v1", "checked_at": stamp(),
            "measured_phase_elapsed_ms": union_ms(measured_intervals + waiting_intervals) - union_ms(waiting_intervals),
            "waiting_for_user_ms": union_ms(waiting_intervals), "phases": list(phases.values()),
            "slow_operations": sorted(slow, key=lambda item: item["elapsed_ms"], reverse=True)[:10],
            "open_span_ids": open_ids,
            "note": "阶段时间按区间并集合计；用户等待单列。命令和查询时间可能嵌套或并行，不能相加为整趟耗时；没有阶段记录的AI阅读与思考未计入。"}
