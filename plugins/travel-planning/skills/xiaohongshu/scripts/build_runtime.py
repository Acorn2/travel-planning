#!/usr/bin/env python3
"""Build the locked MCP source plus the local search-wait repair, in isolation."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
LOCK = SKILL_ROOT / "references" / "upstream.lock.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repair_fingerprint() -> str:
    digest = hashlib.sha256(LOCK.read_bytes())
    for path in (SKILL_ROOT / "references/search-wait.patch", Path(__file__).with_name("search_ready.go")):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def verified_build(binary: Path) -> bool:
    try:
        manifest = json.loads(binary.with_suffix(".build.json").read_text())
        return (manifest["repair_fingerprint"] == repair_fingerprint()
                and manifest["binary_sha256"] == sha256(binary))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def download(url: str, digest: str, destination: Path) -> None:
    if destination.is_file() and sha256(destination) == digest:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".download")
    # Native curl uses the OS trust store (some standalone Python installs lack it).
    subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error",
                    "--connect-timeout", "30", "--max-time", "600",
                    url, "--output", str(temporary)], check=True)
    if sha256(temporary) != digest:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("运行时下载的 SHA256 不匹配")
    temporary.replace(destination)


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()) or member.issym() or member.islnk():
                raise RuntimeError("运行时归档含不安全路径或链接")
        tar.extractall(destination)


def build(binary: Path, data_root: Path) -> None:
    lock = json.loads(LOCK.read_text())
    repair = lock["local_repair"]
    key = f"{platform.system().lower()}-{platform.machine().lower()}"
    toolchain = repair["toolchains"][key]
    cache = data_root / "build-cache"
    go_archive = cache / toolchain["filename"]
    download(f"https://go.dev/dl/{toolchain['filename']}", toolchain["sha256"], go_archive)
    source_archive = cache / f"{lock['commit']}.tar.gz"
    download(f"https://codeload.github.com/xpzouying/xiaohongshu-mcp/tar.gz/{lock['commit']}",
             repair["source_sha256"], source_archive)
    with tempfile.TemporaryDirectory(prefix="repair-", dir=cache) as directory:
        work = Path(directory)
        extract(go_archive, work / "toolchain")
        extract(source_archive, work / "source")
        source = work / "source" / f"xiaohongshu-mcp-{lock['commit']}"
        subprocess.run(["patch", "--batch", "-p1", "-i", str(SKILL_ROOT / "references/search-wait.patch")],
                       cwd=source, check=True)
        for name in ("search_ready.go", "search_ready_test.go"):
            shutil.copyfile(Path(__file__).with_name(name), source / "xiaohongshu" / name)
        go_root = work / "toolchain/go"
        go = go_root / "bin/go"
        environment = {**os.environ, "GOROOT": str(go_root), "GOTOOLCHAIN": "local",
                       "GOWORK": "off", "GOMODCACHE": str(cache / "modules"),
                       "GOCACHE": str(cache / "objects"), "CGO_ENABLED": "0",
                       "GOSUMDB": "sum.golang.org", "GOPROXY": "https://proxy.golang.org,direct"}
        # A fully qualified Go executable prevents changing the user's PATH or Go install.
        subprocess.run([str(go), "test", "-mod=readonly", "./xiaohongshu", "-run",
                        "TestTravelSearch", "-count=1"], cwd=source, env=environment, check=True)
        temporary = work / "server"
        subprocess.run([str(go), "build", "-mod=readonly", "-trimpath", "-buildvcs=false",
                        "-ldflags=-s -w", "-o", str(temporary), "."],
                       cwd=source, env=environment, check=True)
        binary.parent.mkdir(parents=True, exist_ok=True)
        temporary.chmod(0o755)
        temporary.replace(binary)
    manifest = {"upstream_commit": lock["commit"], "repair": repair["version"],
                "go_version": repair["go_version"], "repair_fingerprint": repair_fingerprint(),
                "binary_sha256": sha256(binary)}
    binary.with_suffix(".build.json").write_text(json.dumps(manifest, indent=2) + "\n")
