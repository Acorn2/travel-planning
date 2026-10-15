#!/usr/bin/env python3
"""Create and merge a per-trip research workspace shared by cooperating agents."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


EVIDENCE_SPEC = importlib.util.spec_from_file_location("research_evidence", Path(__file__).with_name("research_evidence.py"))
assert EVIDENCE_SPEC and EVIDENCE_SPEC.loader
research_evidence = importlib.util.module_from_spec(EVIDENCE_SPEC)
EVIDENCE_SPEC.loader.exec_module(research_evidence)

EXECUTION_SPEC = importlib.util.spec_from_file_location("execution_log", Path(__file__).with_name("execution_log.py"))
assert EXECUTION_SPEC and EXECUTION_SPEC.loader
execution_log = importlib.util.module_from_spec(EXECUTION_SPEC)
EXECUTION_SPEC.loader.exec_module(execution_log)


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
WORKSPACE_VERSION = 1
ARCHIVE_KINDS = {"link", "document", "note"}
FRESHNESS_CLASSES = {"stable", "seasonal", "dynamic"}
RESEARCH_STAGES = {"restaurant_discovery", "meal_route_evaluation", "restaurant_ranking"}
DEFAULT_TASK_STAGES = {
    "restaurant-discovery": "restaurant_discovery",
    "route-data-meals": "meal_route_evaluation",
    "restaurant-ranking": "restaurant_ranking",
}
DOCUMENT_EXTENSIONS = {".pdf", ".html", ".htm", ".md", ".txt", ".json", ".csv", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".webp"}
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
SNAPSHOT_ID_PATTERN = re.compile(r"^[a-f0-9]{24}$")
SOURCE_SNAPSHOT_VERSION = "travel-source-snapshot/v1"
INVENTORY_REF_ROLES = {"candidate_quote", "operational_check", "station_lookup"}
INVENTORY_PRODUCTS_BY_DOMAIN = {
    "route-data": {"flight", "train", "air_rail_transfer", "train_station"},
    "transport": {"flight", "train", "air_rail_transfer", "train_station"},
    "stay-food": {"hotel"},
    "stay_food": {"hotel"},
}
TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "assets" / "agent-templates"
DOMAIN_TEMPLATES = {
    "attraction": "attractions.json",
    "attractions": "attractions.json",
    "transport": "route-data.json",
    "route-data": "route-data.json",
    "stay_food": "stay-food.json",
    "stay-food": "stay-food.json",
    "restaurant": "restaurants.json",
    "restaurants": "restaurants.json",
    "restaurant-research": "restaurants.json",
    "weather": "weather-risk.json",
    "weather-risk": "weather-risk.json",
}
DOMAIN_ENTITY_KEYS = {
    "attractions": {"attractions"},
    "attraction": {"attractions"},
    "route-data": {"transport_edges", "intercity_options", "route_anchors", "meal_baseline_routes", "meal_route_evaluations"},
    "transport": {"transport_edges", "intercity_options", "route_anchors", "meal_baseline_routes", "meal_route_evaluations"},
    "stay-food": {"lodging_options", "meal_options"},
    "stay_food": {"lodging_options", "meal_options"},
    "restaurant": {"restaurants", "restaurant_snapshots", "meal_candidate_sets", "meal_options"},
    "restaurants": {"restaurants", "restaurant_snapshots", "meal_candidate_sets", "meal_options"},
    "restaurant-research": {"restaurants", "restaurant_snapshots", "meal_candidate_sets", "meal_options"},
    "weather-risk": {"weather", "event_impacts", "route_impacts"},
    "weather": {"weather", "event_impacts", "route_impacts"},
}
DOMAIN_SHARED_ENTITY_TYPES = {
    "attractions": {"attraction", "place"},
    "attraction": {"attraction", "place"},
    "route-data": {"route_anchor", "transport_hub", "place"},
    "transport": {"route_anchor", "transport_hub", "place"},
    "stay-food": {"lodging", "place"},
    "stay_food": {"lodging", "place"},
    "restaurant": {"restaurant", "place"},
    "restaurants": {"restaurant", "place"},
    "restaurant-research": {"restaurant", "place"},
    "weather-risk": set(),
    "weather": set(),
}
SHARED_ENTITY_TYPES = {
    "attraction", "place", "transport_hub", "lodging", "restaurant", "route_anchor"
}
RESERVED_SHARED_ENTITY_FIELDS = {
    "task_id", "origin_task_ids", "last_submitted_at"
}
COMMUNITY_EVIDENCE_STATUSES = {"available", "partial", "unavailable", "to_recheck"}
COMMUNITY_CONFIDENCE_LEVELS = {"low", "medium"}
COMMUNITY_TARGET_TYPES = {
    "attraction", "restaurant", "lodging", "place", "transport_hub",
    "route_anchor", "transport_edge", "meal", "route", "weather", "trip",
}
COMMUNITY_RECHECK_KEYWORDS = (
    "开放", "施工", "营业", "价格", "预约", "购票", "交通", "停车", "安全",
    "open", "construction", "hours", "price", "reservation", "ticket",
    "transport", "parking", "safety",
)
SOURCE_AUTHORITY_RANKS = {
    "official": 4,
    "official_operator": 4,
    "official_government": 4,
    "official_platform": 4,
    "transport_official": 4,
    "official_venue": 4,
    "map": 3,
    "restaurant_platform": 3,
    "booking_platform": 2,
    "platform": 2,
    "community": 1,
    "xiaohongshu": 1,
}


class WorkspaceError(RuntimeError):
    """The workspace or submitted data violates the shared-state contract."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise WorkspaceError(f"文件不存在：{path}") from error
    except json.JSONDecodeError as error:
        raise WorkspaceError(f"JSON 无效：{path}: {error}") from error


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, payload: Any) -> None:
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def output(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def valid_id(value: str, label: str) -> str:
    if not ID_PATTERN.fullmatch(value):
        raise WorkspaceError(f"{label} 只能使用小写字母、数字、下划线和连字符，最长 64 字符")
    return value


def normalize_community_evidence(item: dict[str, Any], index: int) -> dict[str, Any]:
    """Normalize the XHS comment-insight shape into the workspace contract."""
    if not isinstance(item, dict):
        raise WorkspaceError(f"result.community_evidence[{index}] 必须是对象")
    normalized = dict(item)
    if not normalized.get("insight") and normalized.get("claim"):
        normalized["insight"] = normalized["claim"]
    if not normalized.get("community_confidence") and normalized.get("confidence"):
        normalized["community_confidence"] = normalized["confidence"]
    if not normalized.get("status"):
        normalized["status"] = "to_recheck" if normalized.get("needs_official_recheck") else "available"
    required = {
        "target_entity_id", "target_entity_type", "provider", "checked_at",
        "topic", "insight", "support_count", "contradiction_count",
        "community_confidence", "needs_official_recheck", "source_ids",
    }
    missing = sorted(field for field in required if normalized.get(field) in (None, ""))
    if missing:
        raise WorkspaceError(
            f"result.community_evidence[{index}] 缺少字段：{', '.join(missing)}"
        )
    valid_id(str(normalized["target_entity_id"]), f"result.community_evidence[{index}].target_entity_id")
    if normalized["target_entity_type"] not in COMMUNITY_TARGET_TYPES:
        raise WorkspaceError(
            f"result.community_evidence[{index}].target_entity_type 无效："
            f"{normalized['target_entity_type']}"
        )
    if not isinstance(normalized["provider"], str) or not normalized["provider"].strip():
        raise WorkspaceError(f"result.community_evidence[{index}].provider 必须是非空字符串")
    if normalized["status"] not in COMMUNITY_EVIDENCE_STATUSES:
        raise WorkspaceError(f"result.community_evidence[{index}].status 无效")
    if normalized["community_confidence"] not in COMMUNITY_CONFIDENCE_LEVELS:
        raise WorkspaceError(f"result.community_evidence[{index}].community_confidence 无效")
    if not isinstance(normalized["topic"], str) or not normalized["topic"].strip():
        raise WorkspaceError(f"result.community_evidence[{index}].topic 必须是非空字符串")
    if not isinstance(normalized["insight"], str) or not normalized["insight"].strip():
        raise WorkspaceError(f"result.community_evidence[{index}].insight 必须是非空字符串")
    for field in ("support_count", "contradiction_count"):
        value = normalized[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise WorkspaceError(
                f"result.community_evidence[{index}].{field} 必须是非负整数"
            )
    if normalized["contradiction_count"] > 0:
        normalized["community_confidence"] = "low"
    if normalized["support_count"] <= 1:
        normalized["community_confidence"] = "low"
    if not isinstance(normalized["needs_official_recheck"], bool):
        raise WorkspaceError(
            f"result.community_evidence[{index}].needs_official_recheck 必须是布尔值"
        )
    if not isinstance(normalized["source_ids"], list) or not normalized["source_ids"]:
        raise WorkspaceError(
            f"result.community_evidence[{index}].source_ids 必须是非空数组"
        )
    if len(normalized["source_ids"]) != len(set(str(value) for value in normalized["source_ids"])):
        raise WorkspaceError(f"result.community_evidence[{index}].source_ids 不能重复")
    if parse_datetime(normalized["checked_at"]) is None:
        raise WorkspaceError(f"result.community_evidence[{index}].checked_at 时间格式无效")
    if normalized.get("latest_evidence_at") and parse_datetime(normalized["latest_evidence_at"]) is None:
        raise WorkspaceError(
            f"result.community_evidence[{index}].latest_evidence_at 时间格式无效"
        )
    searchable = f"{normalized['topic']} {normalized['insight']}".casefold()
    if any(keyword.casefold() in searchable for keyword in COMMUNITY_RECHECK_KEYWORDS):
        if not normalized["needs_official_recheck"]:
            raise WorkspaceError(
                f"result.community_evidence[{index}] 涉及开放、营业、价格、预约、交通或安全，"
                "必须标记 needs_official_recheck"
            )
    return normalized


def source_authority_rank(source: dict[str, Any]) -> int:
    explicit = str(source.get("authority") or "").casefold()
    kind = str(source.get("kind") or source.get("source_kind") or "").casefold()
    provider = str(source.get("provider") or "").casefold()
    for value in (explicit, kind, provider):
        for key, rank in SOURCE_AUTHORITY_RANKS.items():
            if key in value:
                return rank
    return 0


def evidence_source_rank(evidence: dict[str, Any], sources_by_id: dict[str, dict[str, Any]]) -> int:
    return max(
        (source_authority_rank(sources_by_id.get(str(source_id), {})) for source_id in evidence.get("source_ids") or []),
        default=0,
    )


def evidence_checked_at(
    evidence: dict[str, Any], sources_by_id: dict[str, dict[str, Any]]
) -> datetime | None:
    evidence_time = parse_datetime(evidence.get("checked_at"))
    source_times = [
        parsed
        for source_id in evidence.get("source_ids") or []
        if (parsed := parse_datetime((sources_by_id.get(str(source_id)) or {}).get("checked_at")))
    ]
    return max([value for value in [evidence_time, *source_times] if value], default=None)


def community_evidence_key(evidence: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(evidence["target_entity_id"]),
        str(evidence["target_entity_type"]),
        str(evidence["topic"]).strip().casefold(),
    )


def revision_digest(workspace: Path, relative_paths: list[str]) -> str:
    hasher = hashlib.sha256()
    for relative in relative_paths:
        path = workspace / relative
        if not path.is_file():
            raise WorkspaceError(f"输入版本文件不存在：{relative}")
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def nested_source_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "source_ids" and isinstance(item, list):
                found.update(str(source_id) for source_id in item)
            else:
                found.update(nested_source_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(nested_source_ids(item))
    return found


def workspace_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    manifest = path / "manifest.json"
    if not manifest.is_file():
        raise WorkspaceError(f"不是有效研究工作区：{path}")
    metadata = read_json(manifest)
    if metadata.get("workspace_version") != WORKSPACE_VERSION:
        raise WorkspaceError(f"不支持的工作区版本：{metadata.get('workspace_version')}")
    return path


def init_workspace(args: argparse.Namespace) -> dict[str, Any]:
    trip_id = valid_id(args.trip_id, "trip_id")
    root = Path(args.root).expanduser().resolve()
    workspace = root / trip_id
    if workspace.exists() and not workspace.is_dir():
        raise WorkspaceError(f"工作区目标是一个文件，不会覆盖：{workspace}")
    if workspace.exists() and any(workspace.iterdir()):
        raise WorkspaceError(f"工作区已存在且非空，不会覆盖：{workspace}")
    for name in ("assignments", "results", "sources", "snapshots", "state", "artifacts", "evidence/main/files"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    brief: dict[str, Any] = {
        "destination": args.destination,
        "date_range": args.date_range,
        "travelers": args.travelers,
        "origin": args.origin,
        "budget": args.budget,
        "lodging_mode": "area",
        "preferences": args.preferences or [],
        "constraints": args.constraints or [],
        "updated_at": now(),
    }
    if args.brief_file:
        extra = read_json(Path(args.brief_file).expanduser().resolve())
        if not isinstance(extra, dict):
            raise WorkspaceError("--brief-file 必须是 JSON 对象")
        brief.update(extra)

    manifest = {
        "workspace_version": WORKSPACE_VERSION,
        "research_contract_version": 1,
        "query_design_version": 1,
        "xhs_comment_research_version": 1,
        "xhs_search_design_version": 1,
        "xhs_detail_sampling_version": 1,
        "trip_id": trip_id,
        "created_at": now(),
        "phase": "route_proposal",
        "write_ownership": {
            "main_agent": ["manifest.json", "brief.json", "route-context.json", "route-proposals.json", "selected-route.json", "state/", "artifacts/", "evidence/main/"],
            "task_agent": ["results/<task_id>.json", "sources/<task_id>.jsonl", "snapshots/<task_id>/", "evidence/<task_id>/"],
        },
        "sensitive_data_policy": "不写入 API Key、Cookie、账号密码、身份证件或支付信息",
        "shared_state_policy": "跨 Agent 复用的数据只写入本行程 workspace；不建立跨行程缓存",
    }
    write_json(workspace / "manifest.json", manifest)
    write_json(workspace / "brief.json", brief)
    write_json(workspace / "route-context.json", {
        "status": "pending",
        "queries": [],
        "checked_at": None,
        "route_signals": [],
        "food_themes": [],
        "conflicts": [],
        "references": [],
        "research_contract": {"version": 1, "query_design_version": 1, "capabilities": {}, "queries": [], "decisions": []},
    })
    write_json(workspace / "route-proposals.json", [])
    return {"status": "created", "workspace": str(workspace), "trip_id": trip_id}


def select_route(args: argparse.Namespace) -> dict[str, Any]:
    workspace = workspace_path(args.workspace)
    route = read_json(Path(args.route_file).expanduser().resolve())
    if not isinstance(route, dict) or not route.get("id"):
        raise WorkspaceError("已选路线必须是包含 id 的 JSON 对象")
    route["selected_at"] = route.get("selected_at") or now()
    write_json(workspace / "selected-route.json", route)
    manifest = read_json(workspace / "manifest.json")
    manifest["phase"] = "confirmed_planning"
    manifest["selected_route_id"] = route["id"]
    manifest["updated_at"] = now()
    write_json(workspace / "manifest.json", manifest)
    return {"status": "selected", "workspace": str(workspace), "route_id": route["id"]}


def assign(args: argparse.Namespace) -> dict[str, Any]:
    workspace = workspace_path(args.workspace)
    task_id = valid_id(args.task_id, "task_id")
    target = workspace / "assignments" / f"{task_id}.json"
    if target.exists():
        raise WorkspaceError(f"任务已存在，不会覆盖：{task_id}")
    selected_route = workspace / "selected-route.json"
    if not selected_route.exists() and args.domain != "route_proposal":
        raise WorkspaceError("路线尚未确认，不应分配深度调研任务")
    stage = getattr(args, "stage", None) or DEFAULT_TASK_STAGES.get(task_id)
    if stage and stage not in RESEARCH_STAGES:
        raise WorkspaceError(f"未知研究阶段：{stage}")
    if stage in {"restaurant_discovery", "restaurant_ranking"} and args.domain not in {"restaurant", "restaurants", "restaurant-research"}:
        raise WorkspaceError(f"{stage} 必须使用 restaurant-research 领域")
    if stage == "meal_route_evaluation" and args.domain not in {"route-data", "transport"}:
        raise WorkspaceError("meal_route_evaluation 必须使用 route-data 领域")
    dependencies = []
    dependency_assignments: list[dict[str, Any]] = []
    for dependency in args.depends_on or []:
        dependency = valid_id(dependency, "depends_on")
        if dependency == task_id:
            raise WorkspaceError("任务不能依赖自己")
        dependency_assignment_path = workspace / "assignments" / f"{dependency}.json"
        if not dependency_assignment_path.exists():
            raise WorkspaceError(f"依赖任务尚未创建：{dependency}")
        dependency_assignment = read_json(dependency_assignment_path)
        dependency_result = workspace / "results" / f"{dependency}.json"
        if not dependency_result.exists():
            raise WorkspaceError(f"依赖任务尚未提交：{dependency}")
        dependency_payload = read_json(dependency_result)
        if dependency_payload.get("status") != "complete":
            raise WorkspaceError(f"依赖任务尚未完成：{dependency}")
        required_identity = {
            "task_id": dependency,
            "domain": dependency_assignment.get("domain"),
            "input_revision": dependency_assignment.get("input_revision"),
            "template_digest": dependency_assignment.get("template_digest"),
        }
        if any(dependency_payload.get(key) != value for key, value in required_identity.items()) or not dependency_payload.get("submitted_at"):
            raise WorkspaceError(f"依赖任务结果不是经 submit 验证的完整结果：{dependency}")
        dependencies.append(dependency)
        dependency_assignments.append(dependency_assignment)
    dependency_stages = {item.get("stage") for item in dependency_assignments}
    if stage == "meal_route_evaluation" and "restaurant_discovery" not in dependency_stages:
        raise WorkspaceError("meal_route_evaluation 必须依赖 restaurant_discovery")
    if stage == "restaurant_ranking" and not {"restaurant_discovery", "meal_route_evaluation"}.issubset(dependency_stages):
        raise WorkspaceError("restaurant_ranking 必须同时依赖 restaurant_discovery 和 meal_route_evaluation")
    read_first = ["manifest.json", "brief.json", "route-context.json"]
    if args.domain != "route_proposal":
        read_first.append("selected-route.json")
    dependency_paths = [f"results/{dependency}.json" for dependency in dependencies]
    template_source = TEMPLATE_ROOT / DOMAIN_TEMPLATES[args.domain] if args.domain in DOMAIN_TEMPLATES else None
    template_relative = f"assignments/templates/{task_id}.result-template.json" if template_source else None
    if template_source:
        atomic_copy(template_source, workspace / template_relative)
    revision_inputs = [*read_first, *dependency_paths, *([template_relative] if template_relative else [])]
    input_revision = revision_digest(workspace, revision_inputs)
    template_digest = hashlib.sha256((workspace / template_relative).read_bytes()).hexdigest() if template_relative else None
    template_version = None
    if template_relative:
        template_payload = read_json(workspace / template_relative)
        template_version = template_payload.get("template_version")
        if not isinstance(template_version, int) or template_version < 1:
            raise WorkspaceError(f"任务模板 {template_relative} 缺少有效 template_version")
    payload = {
        "task_id": task_id,
        "domain": args.domain,
        "stage": stage,
        "instructions": args.instructions,
        "depends_on": dependencies,
        "read_first": read_first,
        "input_paths": [*read_first, *([template_relative] if template_relative else []), f"assignments/{task_id}.json"],
        "dependency_paths": dependency_paths,
        "revision_inputs": revision_inputs,
        "input_revision": input_revision,
        "write_result": f"results/{task_id}.json",
        "write_sources": f"sources/{task_id}.jsonl",
        "owned_paths": [
            f"results/{task_id}.json",
            f"sources/{task_id}.jsonl",
            f"snapshots/{task_id}/",
            f"evidence/{task_id}/",
        ],
        "forbidden_paths": [
            "manifest.json",
            "brief.json",
            "route-context.json",
            "selected-route.json",
            "state/",
            "artifacts/",
            "SKILL.md",
            "scripts/",
            "references/",
        ],
        "source_id_prefix": f"{task_id}-",
        "result_schema_version": "travel-research-result/v2",
        "result_template": template_relative,
        "template_version": template_version,
        "template_digest": template_digest,
        "shared_state_policy": "把其他 Agent、排程、审查或页面会消费的规范化实体写入 shared_entities；merge 后自动进入本行程全局状态",
        "submit_command": f"python3 skills/travel-planning/scripts/research_workspace.py submit --workspace {workspace} --task-id {task_id} --result-file <result.json> --sources-file <sources.json>",
        "created_at": now(),
    }
    write_json(target, payload)
    return {"status": "assigned", "workspace": str(workspace), "assignment": payload}


def validate_result_contract(result: dict[str, Any]) -> None:
    """Keep sub-agent output mergeable instead of accepting prose reports."""
    summary = str(result.get("summary") or "")
    if len(summary) > 200:
        raise WorkspaceError("result.summary 最长 200 字，只写关键结论和阻塞项")
    required_types = {
        "entities": dict,
        "shared_entities": list,
        "event_bindings": list,
        "constraints": list,
        "unresolved": list,
        "source_ids": list,
    }
    for field, expected_type in required_types.items():
        if field not in result or not isinstance(result[field], expected_type):
            raise WorkspaceError(f"result.{field} 必须是 {expected_type.__name__}")
    for lodging in (result.get("entities") or {}).get("lodging_options") or []:
        try:
            research_evidence.validate_lodging_area(lodging)
        except ValueError as error:
            raise WorkspaceError(str(error)) from error
    for meal in (result.get("entities") or {}).get("meal_options") or []:
        if isinstance(meal, dict) and meal.get("mode") == "area":
            try:
                research_evidence.validate_area_meal(meal)
            except ValueError as error:
                raise WorkspaceError(str(error)) from error
    community_evidence = result.get("community_evidence") or []
    if not isinstance(community_evidence, list):
        raise WorkspaceError("result.community_evidence 必须是数组")
    known_entity_ids: dict[str, str] = {}
    for entity in result.get("shared_entities") or []:
        if isinstance(entity, dict) and entity.get("entity_id"):
            known_entity_ids[str(entity["entity_id"])] = str(entity.get("entity_type") or "")
    for collection_name, collection in (result.get("entities") or {}).items():
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if item_id:
                inferred_type = {
                    "restaurants": "restaurant",
                    "restaurant_snapshots": "restaurant",
                    "attractions": "attraction",
                    "lodging_options": "lodging",
                    "transport_edges": "transport_edge",
                    "intercity_options": "route",
                    "meal_options": "meal",
                    "weather": "weather",
                }.get(collection_name, collection_name.rstrip("s"))
                known_entity_ids.setdefault(str(item_id), inferred_type)
            if collection_name == "restaurant_snapshots" and item.get("snapshot_id"):
                known_entity_ids.setdefault(str(item["snapshot_id"]), "restaurant")
    normalized_community_evidence = []
    for index, evidence in enumerate(community_evidence):
        normalized = normalize_community_evidence(evidence, index)
        target_id = str(normalized["target_entity_id"])
        inferred_type = known_entity_ids.get(target_id)
        if inferred_type and normalized["target_entity_type"] not in {
            inferred_type, target_id, "place", "trip"
        }:
            raise WorkspaceError(
                f"result.community_evidence[{index}] 的目标实体类型与结果实体不一致"
            )
        if known_entity_ids and target_id not in known_entity_ids:
            raise WorkspaceError(
                f"result.community_evidence[{index}] 引用了未在本任务结果声明的目标实体：{target_id}"
            )
        normalized_community_evidence.append(normalized)
    result["community_evidence"] = normalized_community_evidence
    snapshot_ids = result.get("source_snapshot_ids") or []
    if not isinstance(snapshot_ids, list):
        raise WorkspaceError("result.source_snapshot_ids 必须是数组")
    if len(snapshot_ids) != len(set(str(value) for value in snapshot_ids)):
        raise WorkspaceError("result.source_snapshot_ids 不能重复")
    for snapshot_id in snapshot_ids:
        if not SNAPSHOT_ID_PATTERN.fullmatch(str(snapshot_id)):
            raise WorkspaceError(f"source_snapshot_id 无效：{snapshot_id}")
    for index, entity in enumerate(result["shared_entities"]):
        if not isinstance(entity, dict):
            raise WorkspaceError(f"result.shared_entities[{index}] 必须是对象")
        reserved = sorted(RESERVED_SHARED_ENTITY_FIELDS.intersection(entity))
        if reserved:
            raise WorkspaceError(
                f"result.shared_entities[{index}] 不能写入合并器保留字段：{', '.join(reserved)}"
            )
        required_entity = {"entity_id", "entity_type", "canonical_name", "facts", "source_ids"}
        missing_entity = sorted(
            field for field in required_entity
            if field not in entity or entity.get(field) in (None, "")
        )
        if missing_entity:
            raise WorkspaceError(
                f"result.shared_entities[{index}] 缺少字段：{', '.join(missing_entity)}"
            )
        valid_id(str(entity["entity_id"]), f"result.shared_entities[{index}].entity_id")
        if entity["entity_type"] not in SHARED_ENTITY_TYPES:
            raise WorkspaceError(
                f"result.shared_entities[{index}].entity_type 无效：{entity['entity_type']}"
            )
        if not isinstance(entity["facts"], dict) or not isinstance(entity["source_ids"], list):
            raise WorkspaceError(
                f"result.shared_entities[{index}] 的 facts 必须是对象且 source_ids 必须是数组"
            )
    domain = result.get("domain")
    if domain in DOMAIN_ENTITY_KEYS:
        unexpected = sorted(set(result["entities"]) - DOMAIN_ENTITY_KEYS[domain])
        if unexpected:
            raise WorkspaceError(f"{domain} 任务不能提交 entities 字段：{', '.join(unexpected)}")
        unexpected_types = sorted(
            {str(item.get("entity_type")) for item in result["shared_entities"]}
            - DOMAIN_SHARED_ENTITY_TYPES[domain]
        )
        if unexpected_types:
            raise WorkspaceError(f"{domain} 任务不能提交共享实体类型：{', '.join(unexpected_types)}")
    for index, binding in enumerate(result["event_bindings"]):
        if not isinstance(binding, dict):
            raise WorkspaceError(f"result.event_bindings[{index}] 必须是对象")
        required = {"id", "day_id", "target", "operation", "snapshot_ids", "event_patch", "source_ids"}
        missing = sorted(field for field in required if field not in binding)
        if missing:
            raise WorkspaceError(f"result.event_bindings[{index}] 缺少字段：{', '.join(missing)}")
        valid_id(str(binding.get("id")), f"result.event_bindings[{index}].id")
        if not isinstance(binding.get("snapshot_ids"), list) or not isinstance(binding.get("event_patch"), dict) or not isinstance(binding.get("source_ids"), list):
            raise WorkspaceError(f"result.event_bindings[{index}] 的 snapshot_ids/source_ids 必须是数组且 event_patch 必须是对象")
        target = binding.get("target") or {}
        allowed_targets = {"day", "attraction", "transport_edge", "meal", "lodging", "checkpoint", "booking_task"}
        if not isinstance(target, dict) or target.get("type") not in allowed_targets or not target.get("entity_id"):
            raise WorkspaceError(f"result.event_bindings[{index}].target 必须包含有效 type 和 entity_id")
        if target.get("type") == "checkpoint" and not target.get("checkpoint_id"):
            raise WorkspaceError(f"result.event_bindings[{index}] 的 checkpoint target 缺少 checkpoint_id")
        if binding.get("operation") not in {"merge", "append_reference", "invalidate"}:
            raise WorkspaceError(f"result.event_bindings[{index}].operation 无效")
    for index, constraint in enumerate(result["constraints"]):
        required_constraint = {"id", "target", "type", "rule", "severity", "source_ids"}
        if not isinstance(constraint, dict) or any(field not in constraint for field in required_constraint):
            raise WorkspaceError(f"result.constraints[{index}] 必须包含 id、target、type、rule、severity、source_ids")
        valid_id(str(constraint.get("id")), f"result.constraints[{index}].id")
        if constraint.get("severity") not in {"blocking", "warning"} or not isinstance(constraint.get("source_ids"), list):
            raise WorkspaceError(f"result.constraints[{index}] 的 severity 或 source_ids 无效")
    for index, unresolved in enumerate(result["unresolved"]):
        required_unresolved = {"id", "target", "field", "reason", "next_action", "recheck_at", "action_links", "severity", "source_ids"}
        if not isinstance(unresolved, dict) or any(field not in unresolved for field in required_unresolved):
            raise WorkspaceError(f"result.unresolved[{index}] 缺少结构化复核字段")
        valid_id(str(unresolved.get("id")), f"result.unresolved[{index}].id")
        if unresolved.get("severity") not in {"blocking", "warning"} or not isinstance(unresolved.get("action_links"), list) or not isinstance(unresolved.get("source_ids"), list):
            raise WorkspaceError(f"result.unresolved[{index}] 的 severity、action_links 或 source_ids 无效")
    if result.get("status") == "complete" and any(item.get("severity") == "blocking" for item in result["unresolved"]):
        raise WorkspaceError("存在 blocking unresolved 时 result.status 不能是 complete")


def validate_source_snapshot(snapshot: dict[str, Any]) -> None:
    required = {
        "schema_version", "snapshot_id", "snapshot_kind", "status", "provider",
        "product_type", "tool", "query", "freshness", "items", "count",
        "raw_response_hash", "source", "disclaimer",
    }
    missing = sorted(field for field in required if field not in snapshot)
    if missing:
        raise WorkspaceError(f"酒旅快照缺少字段：{', '.join(missing)}")
    if snapshot.get("schema_version") != SOURCE_SNAPSHOT_VERSION:
        raise WorkspaceError("酒旅快照 schema_version 无效")
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise WorkspaceError(f"酒旅快照 snapshot_id 无效：{snapshot_id}")
    if snapshot.get("snapshot_kind") not in {"quote", "operational", "lookup"}:
        raise WorkspaceError(f"酒旅快照 {snapshot_id} 的 snapshot_kind 无效")
    if snapshot.get("status") not in {"platform_reported", "no_results"}:
        raise WorkspaceError(f"酒旅快照 {snapshot_id} 的 status 无效")
    provider = snapshot.get("provider") or {}
    if not all(provider.get(field) for field in ("id", "name", "authority", "transport", "package", "version")):
        raise WorkspaceError(f"酒旅快照 {snapshot_id} 缺少 Provider 身份")
    freshness = snapshot.get("freshness") or {}
    if not freshness.get("checked_at") or not freshness.get("expires_at") or freshness.get("dynamic") is not True:
        raise WorkspaceError(f"酒旅快照 {snapshot_id} 必须提供查询与过期时间")
    try:
        checked_at = datetime.fromisoformat(str(freshness["checked_at"]).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(str(freshness["expires_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise WorkspaceError(f"酒旅快照 {snapshot_id} 的时间格式无效") from error
    if expires_at <= checked_at:
        raise WorkspaceError(f"酒旅快照 {snapshot_id} 的 expires_at 必须晚于 checked_at")
    items = snapshot.get("items")
    if not isinstance(items, list) or snapshot.get("count") != len(items):
        raise WorkspaceError(f"酒旅快照 {snapshot_id} 的 count 与 items 不一致")
    offer_ids = []
    for item in items:
        if not isinstance(item, dict) or not item.get("offer_id"):
            raise WorkspaceError(f"酒旅快照 {snapshot_id} 存在无 offer_id 的候选")
        offer_ids.append(str(item["offer_id"]))
        action_link = item.get("action_link")
        if action_link is not None and not str(action_link).startswith("https://"):
            raise WorkspaceError(f"酒旅快照 {snapshot_id} 的 action_link 必须使用 HTTPS")
    if len(offer_ids) != len(set(offer_ids)):
        raise WorkspaceError(f"酒旅快照 {snapshot_id} 的 offer_id 不能重复")
    if snapshot.get("status") == "no_results" and items:
        raise WorkspaceError(f"酒旅快照 {snapshot_id} 标记 no_results 时 items 必须为空")
    source = snapshot.get("source") or {}
    if not str(source.get("url") or "").startswith("https://"):
        raise WorkspaceError(f"酒旅快照 {snapshot_id} 的来源必须使用 HTTPS")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", str(snapshot.get("raw_response_hash") or "")):
        raise WorkspaceError(f"酒旅快照 {snapshot_id} 的原始响应哈希无效")


def store_source_snapshot(workspace: Path, task_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    workspace = workspace_path(str(workspace))
    task_id = valid_id(task_id, "task_id")
    assignment_path = workspace / "assignments" / f"{task_id}.json"
    if not assignment_path.is_file():
        raise WorkspaceError(f"酒旅快照所属任务不存在：{task_id}")
    assignment = read_json(assignment_path)
    allowed_products = INVENTORY_PRODUCTS_BY_DOMAIN.get(str(assignment.get("domain")))
    if not allowed_products:
        raise WorkspaceError("只有 route-data/transport 或 stay-food 任务可以保存酒旅快照")
    validate_source_snapshot(snapshot)
    if snapshot.get("product_type") not in allowed_products:
        raise WorkspaceError(
            f"{assignment.get('domain')} 任务不能保存 {snapshot.get('product_type')} 快照"
        )
    target = workspace / "snapshots" / task_id / f"{snapshot['snapshot_id']}.json"
    if target.exists():
        if read_json(target) != snapshot:
            raise WorkspaceError(f"酒旅快照 ID 冲突，不会覆盖：{snapshot['snapshot_id']}")
        return {"status": "exists", "path": str(target.relative_to(workspace)), "snapshot_id": snapshot["snapshot_id"]}
    write_json(target, snapshot)
    return {"status": "stored", "path": str(target.relative_to(workspace)), "snapshot_id": snapshot["snapshot_id"]}


def load_task_source_snapshots(workspace: Path, task_id: str, snapshot_ids: list[Any]) -> list[dict[str, Any]]:
    snapshots = []
    for value in snapshot_ids:
        snapshot_id = str(value)
        path = workspace / "snapshots" / task_id / f"{snapshot_id}.json"
        snapshot = read_json(path)
        validate_source_snapshot(snapshot)
        if snapshot.get("snapshot_id") != snapshot_id:
            raise WorkspaceError(f"快照文件名与 snapshot_id 不一致：{snapshot_id}")
        snapshots.append(snapshot)
    return snapshots


def validate_inventory_bindings(result: dict[str, Any], snapshots: list[dict[str, Any]]) -> None:
    if not snapshots:
        return
    snapshot_map = {str(snapshot["snapshot_id"]): snapshot for snapshot in snapshots}
    referenced_quotes: set[str] = set()
    domain = str(result.get("domain") or "")
    entities = result.get("entities") or {}
    candidate_groups = []
    if domain in {"route-data", "transport"}:
        candidate_groups.append(("transport_edges", entities.get("transport_edges") or []))
        candidate_groups.append(("intercity_options", entities.get("intercity_options") or []))
    if domain in {"stay-food", "stay_food"}:
        candidate_groups.append(("lodging_options", entities.get("lodging_options") or []))
    for group_name, candidates in candidate_groups:
        for candidate in candidates:
            for ref in candidate.get("inventory_refs") or []:
                if not isinstance(ref, dict) or any(not ref.get(field) for field in ("snapshot_id", "offer_id", "role")):
                    raise WorkspaceError(f"{group_name} 的 inventory_refs 必须包含 snapshot_id、offer_id 和 role")
                if ref["role"] not in INVENTORY_REF_ROLES:
                    raise WorkspaceError(f"{group_name} 的 inventory_ref.role 无效")
                snapshot = snapshot_map.get(str(ref["snapshot_id"]))
                if not snapshot:
                    raise WorkspaceError(f"{group_name} 引用了未声明的酒旅快照：{ref['snapshot_id']}")
                offer_ids = {str(item["offer_id"]) for item in snapshot.get("items") or []}
                if str(ref["offer_id"]) not in offer_ids:
                    raise WorkspaceError(f"{group_name} 的 offer_id 不属于引用快照：{ref['offer_id']}")
                if ref["role"] == "candidate_quote" and snapshot.get("snapshot_kind") != "quote":
                    raise WorkspaceError(f"{group_name} 的 candidate_quote 必须引用 quote 快照")
                if group_name == "lodging_options" and snapshot.get("product_type") != "hotel":
                    raise WorkspaceError("住宿候选只能引用 hotel 快照")
                if group_name in {"transport_edges", "intercity_options"} and snapshot.get("product_type") == "hotel":
                    raise WorkspaceError("城际交通候选不能引用 hotel 快照")
                if snapshot.get("snapshot_kind") == "quote":
                    referenced_quotes.add(str(snapshot["snapshot_id"]))
    unbound_quotes = sorted(
        str(snapshot["snapshot_id"])
        for snapshot in snapshots
        if snapshot.get("snapshot_kind") == "quote" and snapshot.get("items") and str(snapshot["snapshot_id"]) not in referenced_quotes
    )
    if unbound_quotes:
        raise WorkspaceError(f"有结果的报价快照必须投影为候选并绑定：{', '.join(unbound_quotes)}")


def candidate_pairs(payload: dict[str, Any], key: str) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for meal in (payload.get("entities") or {}).get(key) or []:
        meal_id = str(meal.get("id") or "")
        for restaurant_id in meal.get("candidate_ids") or []:
            pairs.add((meal_id, str(restaurant_id)))
    return pairs


def validate_stage_result(workspace: Path, assignment: dict[str, Any], result: dict[str, Any]) -> None:
    """Reject empty or set-incomplete outputs for the staged restaurant workflow."""
    if result.get("status") != "complete" or not assignment.get("stage"):
        return
    stage = assignment["stage"]
    entities = result.get("entities") or {}
    dependencies = [read_json(workspace / path) for path in assignment.get("dependency_paths") or []]
    discovery = next((item for item in dependencies if item.get("stage") == "restaurant_discovery"), None)
    route_result = next((item for item in dependencies if item.get("stage") == "meal_route_evaluation"), None)

    if stage == "restaurant_discovery":
        restaurants = entities.get("restaurants") or []
        snapshots = entities.get("restaurant_snapshots") or []
        candidate_sets = entities.get("meal_candidate_sets") or []
        if not restaurants or not snapshots or not candidate_sets:
            raise WorkspaceError("restaurant_discovery complete 必须包含餐厅、动态快照和逐餐候选集")
        restaurant_ids = {str(item.get("id") or "") for item in restaurants if item.get("id")}
        snapshot_map = {str(item.get("snapshot_id") or ""): item for item in snapshots if item.get("snapshot_id")}
        if len(restaurant_ids) != len(restaurants) or len(snapshot_map) != len(snapshots):
            raise WorkspaceError("restaurant_discovery 的餐厅或快照 ID 缺失或重复")
        for meal in candidate_sets:
            ids = [str(value) for value in meal.get("candidate_ids") or []]
            policy = meal.get("candidate_policy") or {}
            if len(ids) != len(set(ids)):
                raise WorkspaceError("restaurant_discovery 的候选 ID 不能重复")
            if policy.get("status") == "normal" and not 2 <= len(ids) <= 3:
                raise WorkspaceError("restaurant_discovery 的正常餐窗必须有2至3个去重候选")
            if policy.get("status") == "constrained" and any(not policy.get(field) for field in ("restriction_reason", "search_scope", "emergency_fallback", "source_ids")):
                raise WorkspaceError("restaurant_discovery 的受限餐窗缺少原因、搜索范围、应急备选或来源")
            if policy.get("status") not in {"normal", "constrained"}:
                raise WorkspaceError("restaurant_discovery 的 candidate_policy.status 无效")
            if not isinstance(policy.get("searched_count"), int) or policy["searched_count"] < len(ids):
                raise WorkspaceError("restaurant_discovery 必须记录不小于候选数的 searched_count")
            bindings = {str(item.get("restaurant_id") or ""): item for item in meal.get("candidates") or []}
            if set(bindings) != set(ids) or not ids or not set(ids).issubset(restaurant_ids):
                raise WorkspaceError("restaurant_discovery 每个候选必须同时具有餐厅实体和动态快照")
            for restaurant_id, binding in bindings.items():
                snapshot = snapshot_map.get(str(binding.get("snapshot_id") or ""))
                if not snapshot or str(snapshot.get("restaurant_id") or "") != restaurant_id:
                    raise WorkspaceError("restaurant_discovery 每个候选必须绑定匹配的动态快照")
            if not isinstance((meal.get("constraints") or {}).get("max_detour_minutes"), (int, float)):
                raise WorkspaceError("restaurant_discovery 每个餐窗必须给出数值 max_detour_minutes")
            for anchor_name in ("previous_anchor", "next_anchor"):
                anchor = meal.get(anchor_name) or {}
                if not all(anchor.get(field) for field in (("name", "physical_address") if result.get("restaurant_research_version") == 4 else ("name", "physical_address", "coordinates"))):
                    raise WorkspaceError("restaurant_discovery 每个餐窗必须提供含名称、具体地址和坐标的前后锚点")
        if entities.get("meal_options"):
            raise WorkspaceError("restaurant_discovery 不得提前输出最终 meal_options 排名")

    elif stage == "meal_route_evaluation":
        if not discovery:
            raise WorkspaceError("meal_route_evaluation 缺少 discovery 依赖结果")
        expected = candidate_pairs(discovery, "meal_candidate_sets")
        evaluations = entities.get("meal_route_evaluations") or []
        actual = {(str(item.get("meal_id") or ""), str(item.get("restaurant_id") or "")) for item in evaluations}
        baseline_meals = {str(item.get("meal_id") or "") for item in entities.get("meal_baseline_routes") or []}
        expected_meals = {meal_id for meal_id, _ in expected}
        if not expected or actual != expected:
            raise WorkspaceError("route-data-meals 必须且只能覆盖 discovery 的全部餐厅候选")
        if baseline_meals != expected_meals or len(entities.get("meal_baseline_routes") or []) != len(expected_meals):
            raise WorkspaceError("route-data-meals 必须为每餐提供且仅提供一条统一 baseline route")
        discovery_entities = discovery.get("entities") or {}
        meals = {str(item.get("id") or ""): item for item in discovery_entities.get("meal_candidate_sets") or []}
        restaurants = {str(item.get("id") or ""): item for item in discovery_entities.get("restaurants") or []}
        baselines = {str(item.get("meal_id") or ""): item for item in entities.get("meal_baseline_routes") or []}
        for meal_id, restaurant_id in expected:
            meal = meals.get(meal_id) or {}
            baseline = baselines.get(meal_id) or {}
            required_baseline = {"id", "from_anchor", "to_anchor", "mode", "routing_policy", "departure_at", "door_to_door_minutes", "map_url", "checked_at", "source_ids"}
            if result.get("restaurant_research_version") == 4:
                required_baseline.discard("map_url")
            if any(baseline.get(field) in (None, "", []) for field in required_baseline):
                raise WorkspaceError("route-data-meals 的 baseline route 缺少锚点、口径、时间、地图或来源")
            if any(
                any((baseline.get(side) or {}).get(field) != (meal.get(anchor) or {}).get(field) for field in ("name", "physical_address", "coordinates"))
                for side, anchor in (("from_anchor", "previous_anchor"), ("to_anchor", "next_anchor"))
            ):
                raise WorkspaceError("route-data-meals 的 baseline route 锚点与 discovery 餐窗不一致")
            evaluation = next(item for item in evaluations if str(item.get("meal_id") or "") == meal_id and str(item.get("restaurant_id") or "") == restaurant_id)
            if evaluation.get("baseline_route_id") != baseline.get("id"):
                raise WorkspaceError("route-data-meals 的候选没有引用本餐统一 baseline route")
            basis = evaluation.get("comparison_basis") or {}
            if any(basis.get(field) != baseline.get(field) for field in ("mode", "routing_policy", "departure_at")):
                raise WorkspaceError("route-data-meals 的候选路线与 baseline 比较口径不一致")
            restaurant = restaurants.get(restaurant_id) or {}
            restaurant_location = restaurant.get("location") or {}
            expected_ends = (
                ("from_previous", meal.get("previous_anchor") or {}, {"name": restaurant.get("name"), **restaurant_location}),
                ("to_next", {"name": restaurant.get("name"), **restaurant_location}, meal.get("next_anchor") or {}),
            )
            for leg_name, origin, destination in expected_ends:
                leg = evaluation.get(leg_name) or {}
                required_leg = {"route_id", "origin", "destination", "distance_meters", "duration_minutes", "door_to_door_minutes", "map_url"}
                if result.get("restaurant_research_version") == 4:
                    required_leg -= {"distance_meters", "duration_minutes", "map_url"}
                if any(leg.get(field) in (None, "", []) for field in required_leg):
                    raise WorkspaceError("route-data-meals 的候选双腿路线缺少端点、耗时或地图链接")
                route_origin = leg.get("origin") or {}
                route_destination = leg.get("destination") or {}
                if any(not endpoint.get(field) for endpoint in (route_origin, route_destination) for field in (("name", "physical_address") if result.get("restaurant_research_version") == 4 else ("name", "physical_address", "coordinates"))):
                    raise WorkspaceError("route-data-meals 的候选双腿端点必须提供名称、具体地址和坐标")
                if any(route_origin.get(field) != origin.get(field) or route_destination.get(field) != destination.get(field) for field in ("name", "physical_address", "coordinates")):
                    raise WorkspaceError("route-data-meals 的候选双腿端点没有构成上一锚点到餐厅再到下一锚点")
            try:
                total = float(evaluation["from_previous"]["door_to_door_minutes"]) + float(evaluation["to_next"]["door_to_door_minutes"])
                baseline_minutes = float(baseline["door_to_door_minutes"])
                if abs(total - float(evaluation["total_door_to_door_minutes"])) > 0.01 or abs(baseline_minutes - float(evaluation["baseline_door_to_door_minutes"])) > 0.01 or abs(total - baseline_minutes - float(evaluation["detour_minutes"])) > 0.01:
                    raise WorkspaceError("route-data-meals 的候选总耗时、baseline 和绕行计算不一致")
            except (KeyError, TypeError, ValueError) as error:
                if isinstance(error, WorkspaceError):
                    raise
                raise WorkspaceError("route-data-meals 的路线耗时必须是可计算数值") from error

    elif stage == "restaurant_ranking":
        if not discovery or not route_result:
            raise WorkspaceError("restaurant_ranking 缺少 discovery 或 route evaluation 依赖结果")
        expected = candidate_pairs(discovery, "meal_candidate_sets")
        routed = {
            (str(item.get("meal_id") or ""), str(item.get("restaurant_id") or ""))
            for item in (route_result.get("entities") or {}).get("meal_route_evaluations") or []
        }
        ranked = candidate_pairs(result, "meal_options")
        if not expected or expected != routed or expected != ranked:
            raise WorkspaceError("restaurant_ranking 必须完整覆盖 discovery 候选和 route-data-meals 评估")
        if any(entities.get(key) for key in ("restaurants", "restaurant_snapshots", "meal_candidate_sets")):
            raise WorkspaceError("restaurant_ranking 只能输出最终 meal_options，不得复制或新建餐厅实体")
        for meal in entities.get("meal_options") or []:
            ids = [str(value) for value in meal.get("candidate_ids") or []]
            selected = str(meal.get("selected_candidate_id") or "")
            fallbacks = [str(value) for value in meal.get("fallback_candidate_ids") or []]
            bindings = {str(item.get("restaurant_id") or ""): item for item in meal.get("candidates") or []}
            ranks = [item.get("rank") for item in meal.get("candidates") or []]
            invalid_rank = set(bindings) != set(ids) or any(not isinstance(rank, int) for rank in ranks) or sorted(ranks) != list(range(1, len(ids) + 1)) or (selected in bindings and bindings[selected].get("rank") != 1)
            if selected not in ids or any(item not in ids or item == selected for item in fallbacks) or (meal.get("candidate_policy") or {}).get("status") == "normal" and not fallbacks:
                raise WorkspaceError("restaurant_ranking 必须明确合法主选和结构化备选")
            if invalid_rank:
                raise WorkspaceError("restaurant_ranking 必须完整绑定候选并给出连续排名，主选为 rank=1")


def normalize_sources(payload: Any, task_id: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise WorkspaceError("来源文件必须是 JSON 对象或数组")
    sources = []
    for source in payload:
        if not isinstance(source, dict) or not source.get("id") or not source.get("url"):
            raise WorkspaceError("每个来源必须是包含 id 和 url 的 JSON 对象")
        source_id = valid_id(str(source["id"]), "source.id")
        if task_id != "main" and not source_id.startswith(f"{task_id}-"):
            raise WorkspaceError(f"来源 {source_id} 必须使用任务前缀 {task_id}-")
        if not str(source["url"]).startswith("https://"):
            raise WorkspaceError(f"来源 {source_id} 必须使用 HTTPS")
        item = dict(source)
        if item.get("acquisition"):
            try:
                research_evidence.validate_source(item)
            except ValueError as error:
                raise WorkspaceError(str(error)) from error
        item["id"] = source_id
        item["task_id"] = task_id
        item["checked_at"] = item.get("checked_at") or now()
        item["freshness"] = item.get("freshness") or "dynamic"
        if item["freshness"] not in FRESHNESS_CLASSES:
            raise WorkspaceError(f"来源 {source_id} 的 freshness 无效")
        sources.append(item)
    return sources


def validate_restaurant_poi_sources(assignment: dict[str, Any], result: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    if assignment.get("stage") != "restaurant_discovery" or result.get("status") != "complete":
        return
    registry = {str(item.get("id") or ""): item for item in sources}
    for restaurant in (result.get("entities") or {}).get("restaurants") or []:
        restaurant_id = str(restaurant.get("id") or "未命名餐厅")
        location = restaurant.get("location") or {}
        if location.get("verification"):
            try:
                research_evidence.validate_location(restaurant, registry)
                for snapshot in (result.get("entities") or {}).get("restaurant_snapshots") or []:
                    if snapshot.get("restaurant_id") == restaurant.get("id"):
                        research_evidence.validate_evidence(snapshot.get("evidence"), registry, {"identity", "menu", "operations", "experience"})
            except ValueError as error:
                raise WorkspaceError(str(error)) from error
            continue
        poi_id = str(location.get("poi_id") or "")
        provider = str(location.get("poi_provider") or "").casefold()
        source_id = str(location.get("poi_source_id") or "")
        source = registry.get(source_id) or {}
        if not poi_id or not provider or not location.get("poi_verified_at") or source_id not in (restaurant.get("source_ids") or []):
            raise WorkspaceError(f"{restaurant_id} 的 POI 缺少 provider、核验时间或证据来源绑定")
        if source.get("kind") not in {"map", "restaurant_platform"} or str(source.get("provider") or "").casefold() != provider or poi_id not in [str(item) for item in source.get("provider_poi_ids") or []]:
            raise WorkspaceError(f"{restaurant_id} 的 POI 与本任务地图来源证据不一致")


def evidence_record_path(workspace: Path, task_id: str, record_id: str) -> Path:
    task_id = valid_id(task_id, "task_id")
    record_id = valid_id(record_id, "record_id")
    if task_id != "main" and not (workspace / "assignments" / f"{task_id}.json").exists():
        raise WorkspaceError(f"证据所属任务不存在：{task_id}")
    return workspace / "evidence" / task_id / f"{record_id}.json"


def source_evidence_record(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_version": 1,
        "id": source["id"],
        "kind": "link",
        "task_id": source["task_id"],
        "title": source.get("title") or source["url"],
        "url": source["url"],
        "source_kind": source.get("kind"),
        **{key: source[key] for key in ("acquisition", "accessed", "publisher", "published_at", "origin_url", "independence_key") if key in source},
        "provider": source.get("provider"),
        "provider_poi_ids": source.get("provider_poi_ids") or [],
        "summary": source.get("summary") or source.get("note"),
        "query": source.get("query"),
        "location": source.get("location"),
        "topic": source.get("topic"),
        "tags": source.get("tags") or [],
        "checked_at": source["checked_at"],
        "valid_until": source.get("valid_until"),
        "freshness": source["freshness"],
        "archived_at": now(),
    }


def archive(args: argparse.Namespace) -> dict[str, Any]:
    workspace = workspace_path(args.workspace)
    target = evidence_record_path(workspace, args.task_id, args.record_id)
    if target.exists():
        raise WorkspaceError(f"档案记录已存在，不会覆盖：{args.record_id}")
    if args.kind not in ARCHIVE_KINDS:
        raise WorkspaceError(f"kind 只能是：{', '.join(sorted(ARCHIVE_KINDS))}")
    if args.freshness not in FRESHNESS_CLASSES:
        raise WorkspaceError(f"freshness 只能是：{', '.join(sorted(FRESHNESS_CLASSES))}")
    if args.url and not args.url.startswith("https://"):
        raise WorkspaceError("外部来源 URL 必须使用 HTTPS")
    if args.kind == "link" and not args.url:
        raise WorkspaceError("link 档案必须提供 --url")
    if args.kind == "note" and not args.summary:
        raise WorkspaceError("note 档案必须提供 --summary")
    if args.summary and len(args.summary) > 4000:
        raise WorkspaceError("summary 最长 4000 字符；长文档请作为允许留存的文件归档")

    record: dict[str, Any] = {
        "record_version": 1,
        "id": valid_id(args.record_id, "record_id"),
        "kind": args.kind,
        "task_id": valid_id(args.task_id, "task_id"),
        "title": args.title,
        "url": args.url,
        "source_kind": args.source_kind,
        "summary": args.summary,
        "query": args.query,
        "location": args.location,
        "topic": args.topic,
        "tags": args.tag or [],
        "checked_at": args.checked_at or now(),
        "valid_until": args.valid_until,
        "freshness": args.freshness,
        "archived_at": now(),
    }
    if args.kind == "document":
        if not args.file:
            raise WorkspaceError("document 档案必须提供 --file")
        source_file = Path(args.file).expanduser().resolve()
        if not source_file.is_file():
            raise WorkspaceError(f"文档不存在：{source_file}")
        suffix = source_file.suffix.lower()
        if suffix not in DOCUMENT_EXTENSIONS:
            raise WorkspaceError(f"不支持的文档类型：{suffix or '无扩展名'}")
        size = source_file.stat().st_size
        if size > MAX_DOCUMENT_BYTES:
            raise WorkspaceError("单个归档文档不能超过 25 MiB")
        digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
        stored = workspace / "evidence" / args.task_id / "files" / f"{args.record_id}{suffix}"
        if stored.exists():
            raise WorkspaceError(f"归档文件已存在，不会覆盖：{stored}")
        atomic_copy(source_file, stored)
        record.update(
            {
                "original_filename": source_file.name,
                "stored_path": str(stored.relative_to(workspace)),
                "bytes": size,
                "sha256": digest,
            }
        )
    write_json(target, record)
    return {"status": "archived", "workspace": str(workspace), "record": record}


def collect_archive_records(workspace: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted((workspace / "evidence").glob("*/*.json")):
        record = read_json(path)
        if not isinstance(record, dict):
            raise WorkspaceError(f"档案记录必须是 JSON 对象：{path}")
        item = dict(record)
        item["record_path"] = str(path.relative_to(workspace))
        records.append(item)
    return records


def submit(args: argparse.Namespace) -> dict[str, Any]:
    workspace = workspace_path(args.workspace)
    task_id = valid_id(args.task_id, "task_id")
    assignment_path = workspace / "assignments" / f"{task_id}.json"
    assignment = read_json(assignment_path)
    result_path = workspace / "results" / f"{task_id}.json"
    source_path = workspace / "sources" / f"{task_id}.jsonl"
    if result_path.exists() or source_path.exists():
        raise WorkspaceError(f"任务结果已存在，不会覆盖：{task_id}")

    result = read_json(Path(args.result_file).expanduser().resolve())
    if not isinstance(result, dict):
        raise WorkspaceError("任务结果必须是 JSON 对象")
    if result.get("task_id") not in (None, task_id):
        raise WorkspaceError("结果中的 task_id 与提交任务不一致")
    if result.get("input_revision") != assignment.get("input_revision"):
        raise WorkspaceError("结果中的 input_revision 与 assignment 不一致；输入可能已变化")
    if revision_digest(workspace, assignment.get("revision_inputs") or []) != assignment.get("input_revision"):
        raise WorkspaceError("assignment 输入文件已变化；必须重新分配任务")
    if result.get("schema_version") != assignment.get("result_schema_version"):
        raise WorkspaceError("结果 schema_version 与 assignment 不一致")
    if result.get("template_version") != assignment.get("template_version") or result.get("template_digest") != assignment.get("template_digest"):
        raise WorkspaceError("结果模板版本或摘要与 assignment 不一致")
    result["task_id"] = task_id
    result["domain"] = assignment["domain"]
    result["stage"] = assignment.get("stage")
    result["status"] = result.get("status") or "complete"
    if result["status"] not in {"complete", "partial", "blocked"}:
        raise WorkspaceError("result.status 只能是 complete、partial 或 blocked")
    validate_result_contract(result)
    snapshots = load_task_source_snapshots(
        workspace, task_id, result.get("source_snapshot_ids") or []
    )
    validate_inventory_bindings(result, snapshots)
    validate_stage_result(workspace, assignment, result)
    result["submitted_at"] = now()

    sources = []
    if args.sources_file:
        sources = normalize_sources(read_json(Path(args.sources_file).expanduser().resolve()), task_id)
    validate_restaurant_poi_sources(assignment, result, sources)
    available_source_ids = {source["id"] for source in sources}
    referenced_source_ids = nested_source_ids(result)
    missing_source_ids = sorted(referenced_source_ids - available_source_ids)
    if missing_source_ids:
        raise WorkspaceError(f"结果引用了未随任务提交的来源：{', '.join(missing_source_ids)}")
    evidence_targets = []
    for source in sources:
        target = evidence_record_path(workspace, task_id, source["id"])
        if target.exists():
            raise WorkspaceError(f"来源档案已存在，不会覆盖：{source['id']}")
        evidence_targets.append((target, source_evidence_record(source)))
    write_json(result_path, result)
    atomic_write(source_path, "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in sources))
    for target, record in evidence_targets:
        write_json(target, record)
    return {
        "status": "submitted",
        "workspace": str(workspace),
        "task_id": task_id,
        "source_count": len(sources),
        "archive_count": len(evidence_targets),
        "source_snapshot_count": len(snapshots),
    }


def collect_status(workspace: Path) -> dict[str, Any]:
    assignments = sorted((workspace / "assignments").glob("*.json"))
    tasks = []
    for path in assignments:
        task_id = path.stem
        result_path = workspace / "results" / f"{task_id}.json"
        result = read_json(result_path) if result_path.exists() else None
        tasks.append(
            {
                "task_id": task_id,
                "domain": read_json(path).get("domain"),
                "submitted": result is not None,
                "result_status": result.get("status") if result else "pending",
                "source_file": (workspace / "sources" / f"{task_id}.jsonl").exists(),
                "source_snapshot_count": len(list((workspace / "snapshots" / task_id).glob("*.json"))),
            }
        )
    return {
        "workspace": str(workspace),
        "phase": read_json(workspace / "manifest.json").get("phase"),
        "selected_route": (workspace / "selected-route.json").exists(),
        "tasks": tasks,
        "pending_count": sum(not task["submitted"] for task in tasks),
        "archive_count": len(collect_archive_records(workspace)),
        "source_snapshot_count": sum(task["source_snapshot_count"] for task in tasks),
    }


def status(args: argparse.Namespace) -> dict[str, Any]:
    return collect_status(workspace_path(args.workspace))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise WorkspaceError(f"JSONL 无效：{path}:{number}: {error}") from error
        if not isinstance(value, dict):
            raise WorkspaceError(f"JSONL 每行必须是对象：{path}:{number}")
        rows.append(value)
    return rows


def validate_submitted_result(
    workspace: Path,
    assignment: dict[str, Any],
    result: dict[str, Any],
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Revalidate persisted task output before it enters derived state."""
    task_id = str(assignment.get("task_id") or "")
    expected_identity = {
        "task_id": task_id,
        "domain": assignment.get("domain"),
        "stage": assignment.get("stage"),
        "input_revision": assignment.get("input_revision"),
        "schema_version": assignment.get("result_schema_version"),
        "template_version": assignment.get("template_version"),
        "template_digest": assignment.get("template_digest"),
    }
    mismatched = sorted(
        field for field, expected in expected_identity.items()
        if result.get(field) != expected
    )
    if mismatched:
        raise WorkspaceError(
            f"任务 {task_id} 的已提交结果身份不一致：{', '.join(mismatched)}"
        )
    if not result.get("submitted_at"):
        raise WorkspaceError(f"任务 {task_id} 的结果缺少 submitted_at，必须通过 submit 写入")
    if revision_digest(workspace, assignment.get("revision_inputs") or []) != assignment.get("input_revision"):
        raise WorkspaceError(f"任务 {task_id} 的 assignment 输入文件已变化；必须重新分配并提交")
    if result.get("status") not in {"complete", "partial", "blocked"}:
        raise WorkspaceError(f"任务 {task_id} 的 result.status 无效")

    validate_result_contract(result)
    normalized_sources = normalize_sources(sources, task_id)
    if any(source.get("task_id") != task_id for source in sources):
        raise WorkspaceError(f"任务 {task_id} 的来源文件包含错误 task_id")
    snapshots = load_task_source_snapshots(
        workspace, task_id, result.get("source_snapshot_ids") or []
    )
    validate_inventory_bindings(result, snapshots)
    validate_stage_result(workspace, assignment, result)
    validate_restaurant_poi_sources(assignment, result, normalized_sources)
    available_source_ids = {source["id"] for source in normalized_sources}
    missing_source_ids = sorted(nested_source_ids(result) - available_source_ids)
    if missing_source_ids:
        raise WorkspaceError(
            f"任务 {task_id} 的结果引用了未提交来源：{', '.join(missing_source_ids)}"
        )
    return snapshots


def merge_shared_entities(
    results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build one canonical per-trip entity state with deterministic conflict handling."""
    registry: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []

    def merge_value(
        entity_id: str,
        field: str,
        previous: Any,
        incoming: Any,
        previous_tasks: list[str],
        incoming_task: str,
    ) -> Any:
        if previous == incoming:
            return previous
        if isinstance(previous, dict) and isinstance(incoming, dict):
            merged = deepcopy(previous)
            for key, value in incoming.items():
                child = f"{field}.{key}" if field else key
                if key not in merged:
                    merged[key] = deepcopy(value)
                else:
                    merged[key] = merge_value(
                        entity_id, child, merged[key], value, previous_tasks, incoming_task
                    )
            return merged
        conflicts.append({
            "entity_id": entity_id,
            "field": field,
            "previous_value": deepcopy(previous),
            "incoming_value": deepcopy(incoming),
            "selected_value": deepcopy(incoming),
            "previous_task_ids": list(previous_tasks),
            "incoming_task_id": incoming_task,
            "resolution": "latest_submission_wins",
        })
        return deepcopy(incoming)

    ordered_results = sorted(
        results,
        key=lambda item: (str(item.get("submitted_at") or ""), str(item.get("task_id") or "")),
    )
    for result in ordered_results:
        task_id = str(result["task_id"])
        submitted_at = str(result["submitted_at"])
        for incoming in result.get("shared_entities") or []:
            entity_id = str(incoming["entity_id"])
            existing = registry.get(entity_id)
            if existing is None:
                canonical = deepcopy(incoming)
                canonical["source_ids"] = list(dict.fromkeys(canonical.get("source_ids") or []))
                canonical["origin_task_ids"] = [task_id]
                canonical["last_submitted_at"] = submitted_at
                registry[entity_id] = canonical
                continue

            previous_tasks = list(existing.get("origin_task_ids") or [])
            for field, value in incoming.items():
                if field == "entity_id":
                    continue
                if field in {"source_ids", "aliases", "tags"}:
                    existing[field] = list(dict.fromkeys([
                        *(existing.get(field) or []), *(value or [])
                    ]))
                elif field not in existing:
                    existing[field] = deepcopy(value)
                else:
                    existing[field] = merge_value(
                        entity_id, field, existing[field], value, previous_tasks, task_id
                    )
            if task_id not in previous_tasks:
                previous_tasks.append(task_id)
            existing["origin_task_ids"] = previous_tasks
            existing["last_submitted_at"] = submitted_at

    return list(registry.values()), conflicts


def merge_community_evidence(
    results: list[dict[str, Any]],
    sources_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge one current claim per entity/topic without hiding disagreements."""
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    ordered_results = sorted(
        results,
        key=lambda item: (str(item.get("submitted_at") or ""), str(item.get("task_id") or "")),
    )
    for result in ordered_results:
        task_id = str(result["task_id"])
        for index, raw_evidence in enumerate(result.get("community_evidence") or []):
            evidence = normalize_community_evidence(raw_evidence, index)
            key = community_evidence_key(evidence)
            candidate = deepcopy(evidence)
            candidate["origin_task_ids"] = [task_id]
            candidate["source_authority_rank"] = evidence_source_rank(candidate, sources_by_id)
            candidate_time = evidence_checked_at(candidate, sources_by_id)
            candidate["selected_checked_at"] = candidate_time.isoformat(timespec="seconds") if candidate_time else None
            previous = selected.get(key)
            if previous is None:
                selected[key] = candidate
                continue
            if previous.get("insight", "").strip().casefold() == candidate.get("insight", "").strip().casefold():
                previous["source_ids"] = list(dict.fromkeys([
                    *(previous.get("source_ids") or []),
                    *(candidate.get("source_ids") or []),
                ]))
                previous["support_count"] = max(
                    int(previous.get("support_count") or 0),
                    int(candidate.get("support_count") or 0),
                )
                previous["contradiction_count"] = max(
                    int(previous.get("contradiction_count") or 0),
                    int(candidate.get("contradiction_count") or 0),
                )
                previous_time = evidence_checked_at(previous, sources_by_id)
                if candidate_time and (previous_time is None or candidate_time > previous_time):
                    previous["checked_at"] = candidate["checked_at"]
                    previous["latest_evidence_at"] = candidate.get("latest_evidence_at")
                previous["origin_task_ids"] = list(dict.fromkeys([
                    *(previous.get("origin_task_ids") or []), task_id,
                ]))
                previous["needs_official_recheck"] = bool(
                    previous.get("needs_official_recheck") or candidate.get("needs_official_recheck")
                )
                previous["community_confidence"] = (
                    "low"
                    if previous.get("contradiction_count", 0) > 0 or previous.get("support_count", 0) <= 1
                    else "medium"
                )
                continue

            previous_rank = int(previous.get("source_authority_rank") or 0)
            previous_time = evidence_checked_at(previous, sources_by_id)
            candidate_wins = (
                candidate["source_authority_rank"] > previous_rank
                or (
                    candidate["source_authority_rank"] == previous_rank
                    and candidate_time is not None
                    and (previous_time is None or candidate_time > previous_time)
                )
            )
            winner, loser = (candidate, previous) if candidate_wins else (previous, candidate)
            winner["community_confidence"] = "low"
            conflict = {
                "target_entity_id": key[0],
                "target_entity_type": key[1],
                "topic": key[2],
                "previous_value": deepcopy(previous),
                "incoming_value": deepcopy(candidate),
                "selected_value": deepcopy(winner),
                "previous_task_ids": list(previous.get("origin_task_ids") or []),
                "incoming_task_id": task_id,
                "previous_source_ids": list(previous.get("source_ids") or []),
                "incoming_source_ids": list(candidate.get("source_ids") or []),
                "selected_source_ids": list(winner.get("source_ids") or []),
                "selection_reason": (
                    "source_authority"
                    if winner is candidate and candidate["source_authority_rank"] != previous_rank
                    or winner is previous and candidate["source_authority_rank"] != previous_rank
                    else "checked_at"
                ),
                "selected_authority_rank": winner["source_authority_rank"],
                "selected_checked_at": winner.get("selected_checked_at"),
            }
            conflicts.append(conflict)
            winner["origin_task_ids"] = list(dict.fromkeys([
                *(winner.get("origin_task_ids") or []),
                *(loser.get("origin_task_ids") or []),
            ]))
            selected[key] = winner

    output = []
    for item in selected.values():
        item.pop("source_authority_rank", None)
        item.pop("selected_checked_at", None)
        output.append(item)
    return output, conflicts


def merge(args: argparse.Namespace) -> dict[str, Any]:
    workspace = workspace_path(args.workspace)
    snapshot = collect_status(workspace)
    if snapshot["pending_count"] and not args.allow_partial:
        raise WorkspaceError(f'仍有 {snapshot["pending_count"]} 个任务未提交；如确需部分合并使用 --allow-partial')

    assignment_paths = sorted((workspace / "assignments").glob("*.json"))
    assignment_ids = {path.stem for path in assignment_paths}
    result_paths = sorted((workspace / "results").glob("*.json"))
    orphan_result_ids = sorted(path.stem for path in result_paths if path.stem not in assignment_ids)
    if orphan_result_ids:
        raise WorkspaceError(f"存在没有 assignment 的结果：{', '.join(orphan_result_ids)}")
    source_paths = sorted((workspace / "sources").glob("*.jsonl"))
    orphan_source_ids = sorted(
        path.stem for path in source_paths
        if path.stem not in assignment_ids and path.stem != "main"
    )
    if orphan_source_ids:
        raise WorkspaceError(f"存在没有 assignment 的来源文件：{', '.join(orphan_source_ids)}")

    results: list[dict[str, Any]] = []
    sources_by_id: dict[str, dict[str, Any]] = {}
    source_snapshots_by_id: dict[str, dict[str, Any]] = {}
    for assignment_path in assignment_paths:
        task_id = assignment_path.stem
        result_path = workspace / "results" / f"{task_id}.json"
        if not result_path.exists():
            continue
        source_path = workspace / "sources" / f"{task_id}.jsonl"
        if not source_path.is_file():
            raise WorkspaceError(f"任务 {task_id} 缺少已提交来源文件：sources/{task_id}.jsonl")
        assignment = read_json(assignment_path)
        result = read_json(result_path)
        task_sources = read_jsonl(source_path)
        task_snapshots = validate_submitted_result(
            workspace, assignment, result, task_sources
        )
        results.append(result)
        for source in task_sources:
            source_id = source.get("id")
            if not source_id:
                raise WorkspaceError(f"来源缺少 id：{source_path}")
            existing = sources_by_id.get(source_id)
            if existing is not None and existing != source:
                raise WorkspaceError(f"来源 id 冲突：{source_id}")
            sources_by_id[source_id] = source
        for source_snapshot in task_snapshots:
            snapshot_id = str(source_snapshot["snapshot_id"])
            existing = source_snapshots_by_id.get(snapshot_id)
            if existing is not None and existing != source_snapshot:
                raise WorkspaceError(f"酒旅快照 id 冲突：{snapshot_id}")
            source_snapshots_by_id[snapshot_id] = source_snapshot

    main_source_path = workspace / "sources" / "main.jsonl"
    if main_source_path.is_file():
        main_sources = read_jsonl(main_source_path)
        normalized_main_sources = normalize_sources(main_sources, "main")
        if any(source.get("task_id") != "main" for source in main_sources):
            raise WorkspaceError("主 Agent 来源文件包含错误 task_id")
        for source in normalized_main_sources:
            source_id = source["id"]
            existing = sources_by_id.get(source_id)
            if existing is not None and existing != source:
                raise WorkspaceError(f"来源 id 冲突：{source_id}")
            sources_by_id[source_id] = source

    merged_at = now()
    shared_entities, entity_conflicts = merge_shared_entities(results)
    community_evidence, community_conflicts = merge_community_evidence(results, sources_by_id)
    event_bindings = [
        {"task_id": result.get("task_id"), **binding}
        for result in results
        for binding in result.get("event_bindings") or []
    ]
    constraints = [
        {"task_id": result.get("task_id"), **constraint}
        for result in results
        for constraint in result.get("constraints") or []
    ]
    unresolved = [
        {"task_id": result.get("task_id"), **item}
        for result in results
        for item in result.get("unresolved") or []
    ]
    task_index = [{
        "task_id": result.get("task_id"),
        "domain": result.get("domain"),
        "stage": result.get("stage"),
        "status": result.get("status"),
        "summary": result.get("summary") or "",
        "submitted_at": result.get("submitted_at"),
        "input_revision": result.get("input_revision"),
        "result_path": f"results/{result.get('task_id')}.json",
        "source_path": f"sources/{result.get('task_id')}.jsonl",
        "source_snapshot_ids": result.get("source_snapshot_ids") or [],
    } for result in results]
    research = {
        "schema_version": "travel-research-state/v2",
        "trip_id": read_json(workspace / "manifest.json")["trip_id"],
        "selected_route": read_json(workspace / "selected-route.json") if (workspace / "selected-route.json").exists() else None,
        "tasks": task_index,
        "global_state": {
            "shared_entities": shared_entities,
            "conflicts": entity_conflicts,
            "community_evidence": community_evidence,
            "community_evidence_conflicts": community_conflicts,
            "event_bindings": event_bindings,
            "constraints": constraints,
            "unresolved": unresolved,
        },
        "indexes": {
            "sources": "state/sources.json",
            "source_snapshots": "state/source-snapshots.json",
            "archive": "state/archive.json",
        },
        "merged_at": merged_at,
        "partial": bool(snapshot["pending_count"]),
    }
    sources = {"sources": list(sources_by_id.values()), "merged_at": merged_at}
    archive = {"records": collect_archive_records(workspace), "merged_at": merged_at}
    write_json(workspace / "state" / "research.json", research)
    write_json(workspace / "state" / "sources.json", sources)
    write_json(
        workspace / "state" / "source-snapshots.json",
        {"source_snapshots": list(source_snapshots_by_id.values()), "merged_at": merged_at},
    )
    write_json(workspace / "state" / "archive.json", archive)
    return {
        "status": "merged",
        "workspace": str(workspace),
        "result_count": len(results),
        "source_count": len(sources_by_id),
        "source_snapshot_count": len(source_snapshots_by_id),
        "archive_count": len(archive["records"]),
        "shared_entity_count": len(shared_entities),
        "shared_entity_conflict_count": len(entity_conflicts),
        "community_evidence_count": len(community_evidence),
        "community_evidence_conflict_count": len(community_conflicts),
        "partial": research["partial"],
    }


def timing(args: argparse.Namespace) -> dict[str, Any]:
    root = execution_log.workspace_root(args.workspace)
    if args.action == "start":
        if not args.phase:
            raise WorkspaceError("开始阶段计时必须提供 --phase")
        return execution_log.begin(root, args.phase, "phase", task_id=args.task_id)
    if args.action == "finish":
        if not args.span_id:
            raise WorkspaceError("结束阶段计时必须提供 --span-id")
        return execution_log.finish(root, args.span_id, args.result_status)
    payload = execution_log.report(root)
    execution_log.write_json(root / "state/execution-report.json", payload)
    return payload


def query_reuse(args: argparse.Namespace) -> dict[str, Any]:
    """Find exact, recent native-tool queries and their archived source references."""
    root = execution_log.workspace_root(args.workspace)
    parameters = read_json(Path(args.parameters_file).expanduser().resolve())
    if not isinstance(parameters, dict) or not parameters or execution_log.unsafe_payload(parameters):
        raise WorkspaceError("复用查找只接受非空、非敏感的公开查询参数")
    key = execution_log.fingerprint({"provider": args.provider, "tool": args.tool, "parameters": parameters})
    with execution_log.measured(root, args.phase or "research", "native-reuse-lookup", "query") as span:
        span["query_key"] = key
        if args.refresh:
            return {"reusable": False, "reason": "用户要求重新查询"}
        queries = []
        sources = {}
        paths = [root / "route-context.json", root / "state/itinerary-plan.json", root / "artifacts/itinerary.json",
                 *sorted((root / "results").glob("*.json"))]
        for path in paths:
            payload = execution_log.read_json(path)
            if not isinstance(payload, dict):
                continue
            contract = payload.get("research_contract") or (payload.get("planning") or {}).get("research_contract") or {}
            queries.extend(contract.get("queries") or [])
            queries.extend(payload.get("research_queries") or [])
            for source in payload.get("sources") or []:
                if isinstance(source, dict) and isinstance(source.get("id"), str):
                    sources[source["id"]] = source
        sources.update({source.get("id"): source for path in (root / "sources").rglob("*.jsonl") for source in read_jsonl(path)})
        current = datetime.now().astimezone()
        matching = [query for query in queries if isinstance(query, dict)
                    and query.get("provider") == args.provider and query.get("tool") == args.tool
                    and query.get("parameters") == parameters]
        def query_order(query):
            checked = parse_datetime(query.get("checked_at"))
            return checked.timestamp() if checked else float("-inf")
        if matching:
            query = max(matching, key=query_order)
            if query.get("status") != "success":
                return {"reusable": False, "reason": "最新同条件查询未成功；不回退复用旧结果"}
            if not query.get("source_ids"):
                return {"reusable": False, "reason": "最新同条件查询尚未归档已读来源"}
        for query in [max(matching, key=query_order)] if matching else []:
            checked = parse_datetime(query.get("checked_at"))
            if checked is None or not 0 <= (current - checked).total_seconds() < execution_log.REUSE_SECONDS:
                continue
            if not isinstance(query["source_ids"], list) or any(not isinstance(sid, str) or sid not in sources for sid in query["source_ids"]):
                continue
            try:
                for sid in query["source_ids"]:
                    source = sources[sid]
                    research_evidence.validate_source(source)
                    source_checked = parse_datetime(source.get("checked_at"))
                    deadline_value = source.get("expires_at") or source.get("valid_until")
                    deadline = parse_datetime(deadline_value)
                    if (source_checked is None or not 0 <= (current - source_checked).total_seconds() < execution_log.REUSE_SECONDS
                            or (deadline_value and deadline is None)
                            or (deadline is not None and deadline <= current)):
                        raise ValueError("已读来源已过期")
            except ValueError:
                continue
            span["mode"] = "reused"
            return {"reusable": True, "query_id": query.get("id"), "original_checked_at": query["checked_at"],
                    "source_ids": query["source_ids"], "outcome": query.get("outcome"),
                    "note": "复用本行程的已读来源；没有重新请求平台，不能增加独立证据或改写原查询时间"}
        return {"reusable": False, "reason": "未找到同平台、工具、公开参数且仍有效的已读来源"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    command = subparsers.add_parser("init", help="创建本次行程的共享研究工作区")
    command.add_argument("--trip-id", required=True)
    command.add_argument("--root", default=".travel-research")
    command.add_argument("--destination", required=True)
    command.add_argument("--date-range", required=True)
    command.add_argument("--travelers", required=True)
    command.add_argument("--origin")
    command.add_argument("--budget")
    command.add_argument("--preferences", action="append")
    command.add_argument("--constraints", action="append")
    command.add_argument("--brief-file")
    command.set_defaults(handler=init_workspace)

    command = subparsers.add_parser("select-route", help="保存用户已确认的路线")
    command.add_argument("--workspace", required=True)
    command.add_argument("--route-file", required=True)
    command.set_defaults(handler=select_route)

    command = subparsers.add_parser("assign", help="为子 Agent 创建不重叠任务")
    command.add_argument("--workspace", required=True)
    command.add_argument("--task-id", required=True)
    command.add_argument("--domain", required=True)
    command.add_argument("--stage", choices=sorted(RESEARCH_STAGES))
    command.add_argument("--instructions", required=True)
    command.add_argument("--depends-on", action="append")
    command.set_defaults(handler=assign)

    command = subparsers.add_parser("submit", help="原子提交单个子 Agent 的结果和来源")
    command.add_argument("--workspace", required=True)
    command.add_argument("--task-id", required=True)
    command.add_argument("--result-file", required=True)
    command.add_argument("--sources-file")
    command.set_defaults(handler=submit)

    command = subparsers.add_parser("archive", help="把研究链接、笔记或允许留存的文档归档到本次工作区")
    command.add_argument("--workspace", required=True)
    command.add_argument("--record-id", required=True)
    command.add_argument("--task-id", default="main")
    command.add_argument("--kind", choices=sorted(ARCHIVE_KINDS), required=True)
    command.add_argument("--title", required=True)
    command.add_argument("--url", help="原始 HTTPS 来源；link 类型必填")
    command.add_argument("--file", help="document 类型的本地文件")
    command.add_argument("--source-kind", help="official、map、community、booking_platform 等")
    command.add_argument("--summary", help="本次提取的结论；不要粘贴大段网页正文")
    command.add_argument("--query", help="发现该来源时使用的查询条件")
    command.add_argument("--location")
    command.add_argument("--topic")
    command.add_argument("--tag", action="append")
    command.add_argument("--checked-at")
    command.add_argument("--valid-until")
    command.add_argument("--freshness", choices=sorted(FRESHNESS_CLASSES), default="dynamic")
    command.set_defaults(handler=archive)

    command = subparsers.add_parser("status", help="查看任务和共享状态")
    command.add_argument("--workspace", required=True)
    command.set_defaults(handler=status)

    command = subparsers.add_parser("merge", help="由主 Agent 合并任务结果和来源")
    command.add_argument("--workspace", required=True)
    command.add_argument("--allow-partial", action="store_true")
    command.set_defaults(handler=merge)
    command = subparsers.add_parser("timing", help="记录阶段开始/结束，汇总工具耗时、复用与用户等待")
    command.add_argument("--workspace", required=True)
    command.add_argument("--action", choices=["start", "finish", "report"], required=True)
    command.add_argument("--phase", type=execution_log.identifier)
    command.add_argument("--span-id", type=execution_log.identifier)
    command.add_argument("--task-id", type=execution_log.identifier)
    command.add_argument("--result-status", choices=["success", "failed", "cancelled"], default="success")
    command.set_defaults(handler=timing)
    command = subparsers.add_parser("query-reuse", help="直接使用宿主工具前查找可复用的已读来源")
    command.add_argument("--workspace", required=True)
    command.add_argument("--provider", required=True)
    command.add_argument("--tool", required=True)
    command.add_argument("--parameters-file", required=True)
    command.add_argument("--refresh", action="store_true")
    command.set_defaults(handler=query_reuse)
    for name, subparser in subparsers.choices.items():
        if name not in {"timing", "init", "status"}:
            subparser.add_argument("--phase", type=execution_log.identifier, help="本次命令所属阶段，用于执行计时")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command in {"init", "status", "timing"}:
            payload = args.handler(args)
        else:
            phase = getattr(args, "phase", None) or {"select-route": "route_proposal", "merge": "merge"}.get(args.command, "research")
            with execution_log.measured(args.workspace, phase, args.command, task_id=getattr(args, "task_id", None)):
                payload = args.handler(args)
        output(payload)
        return 0
    except (WorkspaceError, execution_log.ExecutionError) as error:
        output({"status": "error", "message": str(error), "checked_at": now()})
        return 2


if __name__ == "__main__":
    sys.exit(main())
