"""smcpriv：powermetrics 解析、采样器源码、plist、读取新鲜度（不碰 root/真文件系统）。"""
import plistlib
import py_compile
import tempfile
from pathlib import Path

import pytest

from workbuddy import smcpriv

# 2026-10-10 MacBookPro16,1 / Ventura 13.2 root 实跑输出节选（M1 闸门）
POWERMETRICS_SAMPLE = """
Machine model: MacBookPro16,1
SMC version: Unknown

**** SMC sensors ****

CPU Thermal level: 100
GPU Thermal level: 55
IO Thermal level: 50
Fan: 5299.57 rpm
CPU die temperature: 86.19 C
GPU die temperature: 78.00 C
CPU Plimit: 0.00
GPU Plimit (Int): 0.00
Number of prochots: 0
"""


class TestParse:
    def test_full_sample(self):
        v = smcpriv.parse_sample(POWERMETRICS_SAMPLE)
        assert v["cputemp"] == 86.2
        assert v["gputemp"] == 78.0
        assert v["fan"] == 5300          # 5299.57 取整

    def test_missing_fields_become_none(self):
        v = smcpriv.parse_sample("Fan: 1200.00 rpm\n")
        assert v["cputemp"] is None
        assert v["gputemp"] is None
        assert v["fan"] == 1200

    def test_empty_output(self):
        v = smcpriv.parse_sample("")
        assert v == {"cputemp": None, "gputemp": None, "fan": None}


class TestSamplerSource:
    def test_compiles(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "sampler.py"
            out.write_text(smcpriv.SAMPLER_SOURCE)
            py_compile.compile(str(out), doraise=True)

    def test_sampler_reads_powermetrics_and_writes_runfile(self):
        src = smcpriv.SAMPLER_SOURCE
        assert "/usr/bin/powermetrics" in src
        assert "0o644" in src            # 世界可读，客户端（用户态）才能读
        assert "workbuddy-smc.json" in src

    def test_sampler_parse_uses_same_patterns(self):
        # 采样器内嵌的正则必须与 parse_sample 一致（防止单测过了、实机跑偏）
        import re
        for pat in (r"CPU die temperature:", r"GPU die temperature:", r"Fan:"):
            inner = smcpriv.SAMPLER_SOURCE.replace("\\\\", "\\")
            assert pat in inner


class TestPlist:
    def test_plist_structure(self):
        pl = plistlib.loads(smcpriv.build_plist())
        assert pl["Label"] == smcpriv.LABEL
        assert pl["StartInterval"] == smcpriv.START_INTERVAL
        assert pl["RunAtLoad"] is True
        assert pl["ProgramArguments"][0] == "/usr/bin/python3"
        assert pl["ProgramArguments"][1].endswith("smc_sampler.py")


class TestRead:
    def _write_run(self, monkeypatch, payload, tmp_path):
        run = tmp_path / "run.json"
        run.write_text(payload)
        monkeypatch.setattr(smcpriv, "RUN_FILE", run)
        return run

    def test_fresh_sample(self, monkeypatch, tmp_path):
        import time
        self._write_run(monkeypatch,
                        '{"at": %f, "cputemp": 55.0, "gputemp": null, "fan": 1800}'
                        % time.time(), tmp_path)
        v = smcpriv.read()
        assert v["cputemp"] == 55.0
        assert v["fan"] == 1800

    def test_stale_sample_rejected(self, monkeypatch, tmp_path):
        self._write_run(monkeypatch,
                        '{"at": 1000, "cputemp": 55.0, "fan": 1800}', tmp_path)
        assert smcpriv.read() is None

    def test_err_sample_rejected(self, monkeypatch, tmp_path):
        import time
        self._write_run(monkeypatch,
                        '{"at": %f, "err": "boom"}' % time.time(), tmp_path)
        assert smcpriv.read() is None

    def test_missing_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(smcpriv, "RUN_FILE", tmp_path / "nope.json")
        assert smcpriv.read() is None


class TestStatus:
    def test_not_installed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(smcpriv, "PLIST_PATH", tmp_path / "x.plist")
        monkeypatch.setattr(smcpriv, "RUN_FILE", tmp_path / "x.json")
        s = smcpriv.status()
        assert s == {"installed": False, "fresh": False, "age": None,
                     "data": None, "platform": "darwin", "interval": 20}

    def test_installed_fresh(self, monkeypatch, tmp_path):
        import time
        (tmp_path / "p.plist").write_bytes(smcpriv.build_plist())
        run = tmp_path / "r.json"
        run.write_text('{"at": %f, "cputemp": 60.0, "fan": 2000}' % time.time())
        monkeypatch.setattr(smcpriv, "PLIST_PATH", tmp_path / "p.plist")
        monkeypatch.setattr(smcpriv, "RUN_FILE", run)
        s = smcpriv.status()
        assert s["installed"] and s["fresh"]
        assert s["data"]["cputemp"] == 60.0


class TestElevate:
    def test_cancel_is_friendly_error(self, monkeypatch, tmp_path):
        import subprocess

        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "0:113: execution error: User canceled. (-128)"

        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: FakeProc())
        script = tmp_path / "s.sh"
        script.write_text("true")
        with pytest.raises(RuntimeError, match="取消"):
            smcpriv._elevate(script, timeout=5)


def test_build_plist_uses_given_interval():
    import plistlib
    pl = plistlib.loads(smcpriv.build_plist(120))
    assert pl["StartInterval"] == 120
    assert plistlib.loads(smcpriv.build_plist())["StartInterval"] == 20


def test_stored_interval_roundtrip(tmp_path):
    import json
    meta = tmp_path / "smc.json"
    meta.write_text(json.dumps({"interval": 120}))
    orig = smcpriv.META_FILE
    smcpriv.META_FILE = meta
    try:
        assert smcpriv.stored_interval() == 120
        meta.write_text("not json")
        assert smcpriv.stored_interval() == 20
    finally:
        smcpriv.META_FILE = orig


def test_read_freshness_follows_stored_interval(tmp_path):
    import json
    import time
    meta = tmp_path / "smc.json"
    meta.write_text(json.dumps({"interval": 300}))
    run = tmp_path / "run.json"
    run.write_text('{"at": %f, "cputemp": 60.0, "fan": 2000}' % (time.time() - 200))
    orig_meta, orig_run = smcpriv.META_FILE, smcpriv.RUN_FILE
    smcpriv.META_FILE, smcpriv.RUN_FILE = meta, run
    try:
        # 200s 前的样本：固定 60s 窗口会拒收，自适应窗口（300*3）应接受
        assert smcpriv.read(fresh_window=60) is None
        assert smcpriv.read()["cputemp"] == 60.0
    finally:
        smcpriv.META_FILE, smcpriv.RUN_FILE = orig_meta, orig_run
