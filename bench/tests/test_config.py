import os

from bench.config import load_settings


def test_load_settings_reads_file_only(tmp_path, monkeypatch):
    env_file = tmp_path / "bench.env"
    env_file.write_text(
        "BENCH_JUDGE_MODEL=claude-sonnet-4-6\n"
        "BENCH_FOUNDRY_RESOURCE=r1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BENCH_FOUNDRY_RESOURCE", "shell")

    settings = load_settings(env_file)

    assert settings.foundry_resource == "r1"
    assert settings.judge_model == "claude-sonnet-4-6"


def test_defaults(tmp_path):
    env_file = tmp_path / "bench.env"
    env_file.write_text("", encoding="utf-8")

    settings = load_settings(env_file)

    assert settings.judge_model == "claude-sonnet-4-6"
    assert settings.base_port == 8100
    assert settings.turn_timeout_s == 120.0


def test_missing_file_uses_defaults(tmp_path):
    missing = tmp_path / "does-not-exist.env"

    settings = load_settings(missing)

    assert settings.judge_model == "claude-sonnet-4-6"
    assert settings.mongo_uri == "mongodb://localhost:27017"
    assert settings.admin_phone == "0901234567"
    assert settings.admin_password == "chutiem123"


def test_repo_root_and_dirs_derived_from_package_location():
    settings = load_settings(None)

    assert (settings.repo_root / "bench").is_dir()
    assert settings.results_dir == settings.repo_root / "bench" / "results"
    assert settings.worktrees_dir == settings.repo_root / "bench" / ".worktrees"
