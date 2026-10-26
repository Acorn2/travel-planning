"""Copy a standalone itinerary page into a visible, static-host-ready folder."""

from __future__ import annotations

import argparse
import importlib.util
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


COMMENT_SPEC = importlib.util.spec_from_file_location("share_comment_contract", Path(__file__).with_name("xhs_comment_contract.py"))
assert COMMENT_SPEC and COMMENT_SPEC.loader
xhs_comment_contract = importlib.util.module_from_spec(COMMENT_SPEC)
COMMENT_SPEC.loader.exec_module(xhs_comment_contract)


class LocalDependencyScanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.dependencies: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self._check(values["src"])
        if tag == "link" and values.get("href") and "stylesheet" in (values.get("rel") or "").split():
            self._check(values["href"])
        if tag in {"img", "iframe", "video", "audio", "source"} and values.get("src"):
            self._check(values["src"])

    def _check(self, value: str) -> None:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"https", "http", "data"}:
            self.dependencies.append(value)


def prepare_share(source: Path, output_dir: Path, *, replace: bool = False) -> Path:
    if not source.is_file():
        raise ValueError(f"找不到 HTML 文件：{source}")
    data = source.read_bytes()
    xhs_comment_contract.validate_page_evidence(source, data.decode("utf-8"))
    scanner = LocalDependencyScanner()
    scanner.feed(data.decode("utf-8"))
    if scanner.dependencies:
        raise ValueError(f"HTML 仍依赖本地资源：{', '.join(scanner.dependencies)}")
    target = output_dir / "index.html"
    if source.resolve() == target.resolve():
        return target
    if target.exists() and target.read_bytes() != data and not replace:
        raise FileExistsError(f"分享目录已有不同的 index.html：{target}")
    output_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="渲染后的 itinerary.html")
    parser.add_argument("--output-dir", required=True, type=Path, help="可见的分享文件夹")
    parser.add_argument("--replace", action="store_true", help="更新同一行程已有的 index.html")
    args = parser.parse_args()
    target = prepare_share(args.source, args.output_dir, replace=args.replace)
    print(f"分享文件：{target.resolve()}")
    print("这是本地文件；通过隔空投送或网盘传到手机，或经授权发布到静态托管后获取公开链接。")


if __name__ == "__main__":
    main()
