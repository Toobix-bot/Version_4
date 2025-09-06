import os
import runpy
import types


def test_env_checker_missing(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    mod = runpy.run_path("scripts/check_env.py")
    # main() returns exit code
    assert mod["validate_api_key"](None) == 1


def test_env_checker_pattern(monkeypatch):
    monkeypatch.setenv("API_KEY", "not_valid")
    mod = runpy.run_path("scripts/check_env.py")
    assert mod["validate_api_key"]("not_valid") == 2


def test_env_checker_ok(monkeypatch):
    monkeypatch.setenv("API_KEY", "gsk_" + "x" * 50)
    mod = runpy.run_path("scripts/check_env.py")
    assert mod["validate_api_key"]("gsk_" + "x" * 50) == 0
