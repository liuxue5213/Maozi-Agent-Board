"""UsageScanner 空 root 守卫回归。

Bug：roots 里某项为空字符串时 `Path("")` 退化成当前目录（`.is_dir()` 为真），
于是 claude 的 glob("*/*.jsonl") 与 codex 的 rglob("rollout-*.jsonl") 会去扫
运行目录整棵树——既慢，又会把无关 *.jsonl 混进用量，表现为「依赖真实会话
数据时用量用例偶发失败」。
"""
import datetime
import json

from workbuddy.aiwatch.usage import UsageScanner


def _today_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat()


def test_root_dir_guards_blank_none_and_missing(tmp_path):
    sc = UsageScanner({"claude": "", "codex": "   ", "zcode": None, "real": str(tmp_path)},
                      trend_path=tmp_path / "trend.json")
    assert sc._root_dir("claude") is None     # 空串
    assert sc._root_dir("codex") is None      # 纯空白
    assert sc._root_dir("zcode") is None      # None
    assert sc._root_dir("nope") is None       # 未配置的键
    assert sc._root_dir("real") == tmp_path   # 正常路径保留


def test_empty_roots_never_scan_cwd(tmp_path, monkeypatch):
    """空 root 时，cwd 里的诱饵 jsonl 绝不能被当成会话数据。"""
    # claude 诱饵：cwd/sub/s1.jsonl（会被 glob("*/*.jsonl") 命中）
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "s1.jsonl").write_text(
        json.dumps({"type": "assistant", "timestamp": _today_iso(),
                    "message": {"model": "m", "usage": {"input_tokens": 10,
                                                        "output_tokens": 2}}}) + "\n",
        encoding="utf-8")
    # codex 诱饵：cwd/rollout-demo.jsonl（会被 rglob("rollout-*.jsonl") 命中）
    (tmp_path / "rollout-demo.jsonl").write_text(
        json.dumps({"type": "token_usage_record", "timestamp": _today_iso(),
                    "payload": {"usage": {"total_tokens": 7, "output_tokens": 1}}}) + "\n",
        encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    sc = UsageScanner({"claude": "", "codex": "", "zcode": ""},
                      trend_path=tmp_path / "trend.json")
    sc._scan_once()

    assert sc.sessions_by_tool == {}, "空 root 不应产生任何会话"
    assert sc.today["total"] == 0, "空 root 不应把 cwd 的诱饵计入今日用量"


def test_real_root_over_tmp_is_scanned(tmp_path, monkeypatch):
    """反证：给了真实 root 时，同样结构的诱饵应被正常统计（守卫没有误伤）。"""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "s1.jsonl").write_text(
        json.dumps({"type": "assistant", "timestamp": _today_iso(),
                    "message": {"model": "m", "usage": {"input_tokens": 10,
                                                        "output_tokens": 2}}}) + "\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path / "sub")   # cwd 与 root 不同，确保数据来自 root
    sc = UsageScanner({"claude": str(tmp_path), "codex": "", "zcode": ""},
                      trend_path=tmp_path / "trend.json")
    sc._scan_once()
    assert sc.sessions_by_tool.get("claude") == 1
    assert sc.today["total"] == 12
