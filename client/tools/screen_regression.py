#!/usr/bin/env python3
"""实屏回归：按场景切配置 → 等设备应用 → 实拍存档 → 恢复原配置。

为什么要这个工具：Web 预览与固件几何再同源也可能有差异（裁切/色带/字库），
核心页面必须用真机画面验收。实拍链路 1.4s 后（串口提速）这事的成本已经
足够低，值得脚本化。

**安全约束**（务必保持）：
  · 必须显式 `--run` 才动手，否则只打印计划；
  · 启动先经 /api/export 备份当前配置，finally 里 /api/save 原样恢复——
    绝不让测试把用户的主题/页面留在设备上（历史上真踩过）；
  · 保存/同步只走产品路径 /api/save + /api/sync，不走手种 CFG（设备不认）。

用法：
    python3 tools/screen_regression.py            # 打印计划
    python3 tools/screen_regression.py --run      # 执行默认场景
    python3 tools/screen_regression.py --run --only 紫夜
    python3 tools/screen_regression.py --run --out /tmp/shots
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
# 存档目录锚定仓库根（client/tools/ 的上两级）：从任何 CWD 运行产物都落同一处，
# 不会在工作目录里散落 client/outputs/ 这类一次性目录
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = _REPO_ROOT / "outputs" / "screen-regression"

# 默认场景：一套基线 + 两个差异大的主题（深色→浅色，验证边界与文字对比度）
SCENARIOS = [
    {"name": "baseline", "desc": "当前配置不动，仅存档基线", "theme": None},
    {"name": "紫夜", "desc": "深紫主题（card_opa 72）",
     "theme": {"bg": "120B22", "grad": "1D1136", "card": "4D3C7A",
               "card_edge": "54427F", "accent": "A78BFA", "text": "F5F3FF",
               "sub": "C4B5E8", "card_opa": 72}},
    {"name": "墨韵", "desc": "浅色主题（背景亮，验证暗字可读性）",
     "theme": {"bg": "D2CFCA", "grad": "C9C6BD", "card": "FFFFFF",
               "card_edge": "AEA89C", "accent": "B03A2E", "text": "2B2B2B",
               "sub": "6B6B60", "card_opa": 93}},
]


def _req(path, body=None, raw=False, timeout=30):
    if body is None:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.read() if raw else json.load(r)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _post_raw(path: str, payload: bytes, timeout=60):
    req = urllib.request.Request(BASE + path, data=payload,
                                 headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def wait_applied(target_version: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = _req("/api/status").get("profile_status", "")
            applied = _req("/api/status").get("applied_version")
        except OSError:
            time.sleep(1)
            continue
        if applied == target_version:
            return True
        if status == "同步失败":
            return False
        time.sleep(1)
    return False


def snapshot(out_path: Path, timeout: float = 30.0) -> bool:
    _req("/api/snap-request", {})
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            at = _req("/api/snapshot-meta").get("at")
        except OSError:
            at = None
        if at:
            png = _req(f"/api/snapshot?ts={at}", raw=True)
            out_path.write_bytes(png)
            return len(png) > 1000
        time.sleep(1)
    return False


def run(only: str | None, out_dir: Path) -> int:
    status = _req("/api/status")
    if not str(status.get("device", "")).startswith("connected:"):
        print("设备未连接，无法实拍")
        return 2
    original = _req("/api/export")
    print(f"已备份当前配置 v{original.get('version')}")
    failures = 0
    try:
        for scene in SCENARIOS:
            if only and scene["name"] != only:
                continue
            profile = json.loads(json.dumps(original, ensure_ascii=False))
            if scene["theme"]:
                profile["theme"].update(scene["theme"])
            saved = _req("/api/save", profile)["profile"]
            _req("/api/sync", {})
            ok = wait_applied(saved["version"])
            if not ok:
                print(f"[{scene['name']}] 应用失败（v{saved['version']}），跳过实拍")
                failures += 1
                continue
            out = out_dir / f"{time.strftime('%m%d-%H%M%S')}-{scene['name']}.png"
            # 应用重建后缓冲一拍再拍：固件曾因「重建同拍实拍」panic（已修，
            # 这里加缓冲是双保险）；超时重试一次
            time.sleep(1.5)
            if snapshot(out) or snapshot(out):
                print(f"[{scene['name']}] ✓ {out}")
            else:
                print(f"[{scene['name']}] ✗ 实拍超时")
                failures += 1
    finally:
        # 无论成败，把用户的配置原样放回去（版本 +2 是产品路径的正常代价）
        restored = _req("/api/save", original)["profile"]
        _req("/api/sync", {})
        wait_applied(restored["version"])
        print(f"已恢复原配置（v{restored['version']}，已同步设备）")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="实屏截图回归")
    parser.add_argument("--run", action="store_true", help="真的执行（默认只打印计划）")
    parser.add_argument("--only", default="", help="只跑指定场景名")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="PNG 存档目录（默认 仓库根/outputs/screen-regression）")
    args = parser.parse_args()
    out_dir = Path(args.out)
    if not args.run:
        print("计划（加 --run 执行）：")
        for s in SCENARIOS:
            print(f"  · {s['name']}：{s['desc']}")
        print(f"  · 存档到 {out_dir}/，结束后恢复当前配置")
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    return run(args.only or None, out_dir)


if __name__ == "__main__":
    sys.exit(main())
