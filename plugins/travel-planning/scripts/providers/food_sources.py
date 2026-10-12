#!/usr/bin/env python3
"""Optional read-only food discovery MCP, with an isolated browser runtime."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import venv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import food_browser as backend


TOOLS = {
    "food_status": ("检查可选运行时；本地 profile 存在不代表登录有效", {"platform": {"enum": list(backend.PLATFORMS)}}),
    "douyin_search": ("实验性抖音关键词搜索；最多5条文字线索，不读取视频口播", {"keyword": {"type": "string", "minLength": 1, "maxLength": 100}, "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3}}),
    "douyin_detail": ("读取搜索所得视频页面的文字说明；无下载、转录或评论", {"url": {"type": "string"}}),
    "dianping_search": ("实验性点评商户搜索，先从平台选择指定城市；最多5家", {"city": {"type": "string", "minLength": 1, "maxLength": 40}, "keyword": {"type": "string", "minLength": 1, "maxLength": 100}, "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3}}),
    "dianping_detail": ("读取搜索所得点评门店的页面字段；缺失字段明确标记，无完整评价", {"url": {"type": "string"}}),
}


def tool_list() -> list[dict]:
    return [{"name": name, "description": desc,
             "inputSchema": {"type": "object", "properties": fields,
                             "required": [k for k in fields if k != "limit"], "additionalProperties": False},
             "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}}
            for name, (desc, fields) in TOOLS.items()]


class FoodServer:
    def __init__(self):
        self.calls = 0
        self.blocked: dict[str, str] = {}
        self.observed: dict[str, set[str]] = {p: set() for p in backend.PLATFORMS}

    def call(self, name: str, arguments: dict) -> dict:
        if name not in TOOLS:
            raise ValueError("未知或非只读工具")
        if not isinstance(arguments, dict):
            raise ValueError("arguments 必须是对象")
        fields = TOOLS[name][1]
        if set(arguments) - set(fields) or any(k not in arguments for k in fields if k != "limit"):
            raise ValueError("工具参数缺失或包含未允许字段")
        for key, spec in fields.items():
            value = arguments.get(key, spec.get("default"))
            if spec.get("type") == "string" and (not isinstance(value, str) or not value.strip() or len(value) > spec.get("maxLength", 500)):
                raise ValueError(f"{key} 必须是有效短文本")
            if spec.get("type") == "integer" and (type(value) is not int or not spec["minimum"] <= value <= spec["maximum"]):
                raise ValueError(f"{key} 必须为1至5的整数")
            if "enum" in spec and value not in spec["enum"]:
                raise ValueError(f"{key} 值无效")
        platform = arguments.get("platform") if name == "food_status" else name.split("_", 1)[0]
        if name == "food_status":
            return backend.runtime_status(platform)
        if platform in self.blocked:
            raise backend.FoodError(self.blocked[platform], "本会话该来源已停止；处理后新建会话，不重复查询")
        if self.calls >= 20:
            raise backend.FoodError("query_budget_exhausted", "本会话已达到20次读取上限，请复用已有结果")
        params = dict(arguments)
        if name.endswith("_detail"):
            kind = "video" if platform == "douyin" else "shop"
            url = backend.public_url(params["url"], platform, kind=kind)
            if not url or url not in self.observed[platform]:
                raise ValueError("详情必须引用本会话搜索实际返回的公开链接")
            params["url"] = url
        if name.endswith("_search"):
            params.setdefault("limit", 3)
        self.calls += 1
        try:
            payload = backend.result(platform, getattr(backend, name)(**params))
        except backend.FoodError as error:
            if error.kind != "no_results":
                self.blocked[platform] = error.kind
            raise
        except Exception:
            self.blocked[platform] = "query_failed"
            # Browser exceptions can contain page responses or private session values.
            raise backend.FoodError("query_failed", "页面读取失败，已停止该来源；使用已有线索或地图兜底") from None
        self.observed[platform].update(item["url"] for item in payload.get("items", []))
        return payload


def rpc(server: FoodServer, request: dict) -> dict | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in request:
        return None
    response = {"jsonrpc": "2.0", "id": request["id"]}
    method, params = request.get("method"), request.get("params") or {}
    if method == "initialize":
        protocol = params.get("protocolVersion", "2024-11-05")
        response["result"] = {"protocolVersion": protocol if protocol in {"2024-11-05", "2025-03-26", "2025-06-18"} else "2024-11-05",
                              "capabilities": {"tools": {}},
                              "serverInfo": {"name": "travel-food-discovery", "version": "0.1.0"}}
    elif method == "ping":
        response["result"] = {}
    elif method == "tools/list":
        response["result"] = {"tools": tool_list()}
    elif method == "tools/call":
        try:
            payload = server.call(params.get("name"), params.get("arguments", {}))
            failed = False
        except (ValueError, backend.FoodError) as error:
            payload = {"status": "unavailable", "failure_kind": getattr(error, "kind", "invalid_arguments"), "message": str(error)}
            failed = True
        response["result"] = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "isError": failed}
    else:
        response["error"] = {"code": -32601, "message": "Method not found"}
    return response


def serve() -> int:
    server = FoodServer()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = rpc(server, request)
        except (json.JSONDecodeError, TypeError, AttributeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON-RPC message"}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


def runtime_python() -> Path:
    return backend.ROOT / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stdio")
    commands.add_parser("install", help="只安装用户数据目录下的可选 Playwright 运行时，不下载 Chrome")
    login = commands.add_parser("login", help="打开专用 Chrome，由本人登录；不索要 Cookie")
    login.add_argument("platform", choices=backend.PLATFORMS)
    status = commands.add_parser("status", help="仅检查本地运行时，不证明在线登录有效")
    status.add_argument("platform", choices=backend.PLATFORMS)
    args = parser.parse_args()
    if args.command == "install":
        backend.ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        venv.EnvBuilder(with_pip=True).create(backend.ROOT / "venv")
        subprocess.run([str(runtime_python()), "-m", "pip", "install", f"playwright=={backend.PLAYWRIGHT_VERSION}"], check=True)
        print("可选运行时已安装；请分别运行 login douyin 和 login dianping，由本人完成登录")
        return 0
    executable = runtime_python()
    # Venv executable is usually a symlink; compare sys.prefix, not real executable.
    if executable.is_file() and Path(sys.prefix) != executable.parent.parent:
        os.execv(str(executable), [str(executable), str(Path(__file__).resolve()), *sys.argv[1:]])
    if args.command == "stdio":
        return serve()
    if args.command == "status":
        print(json.dumps(backend.runtime_status(args.platform), ensure_ascii=False))
        return 0
    with backend.browser(args.platform, login=True) as page:
        page.goto(backend.PLATFORMS[args.platform], wait_until="domcontentloaded")
        input("请在专用窗口完成登录和平台验证，完成后按回车关闭窗口：")
        backend.check_page(page, args.platform)
    print("专用浏览器资料已保存；查询时仍会检查访问限制，不代表已通过真实搜索验收")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (backend.FoodError, subprocess.CalledProcessError, KeyboardInterrupt) as error:
        print("操作停止" if isinstance(error, KeyboardInterrupt) else str(error), file=sys.stderr)
        sys.exit(2)
