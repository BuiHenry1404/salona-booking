"""Cấu hình cho benchmark — đọc từ file `bench/.env`, KHÔNG đọc biến môi trường
shell (cùng quy ước với `app/config.py` của repo chính, xem AGENTS.md).

`load_settings` tự parse file dạng `KEY=VALUE` dòng-theo-dòng; không dùng
python-dotenv và không rơi về `os.environ` khi thiếu khoá.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Thư mục gốc repo = cha của gói bench/.
_REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ENV_PATH = Path(__file__).resolve().parent / ".env"


@dataclass
class BenchSettings:
    repo_root: Path = field(default_factory=lambda: _REPO_ROOT)
    results_dir: Path = field(default_factory=lambda: _REPO_ROOT / "bench" / "results")
    worktrees_dir: Path = field(default_factory=lambda: _REPO_ROOT / "bench" / ".worktrees")

    base_port: int = 8100
    mongo_uri: str = "mongodb://localhost:27017"
    judge_model: str = "claude-sonnet-4-6"

    foundry_resource: str | None = None
    foundry_api_key: str | None = None

    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    admin_phone: str = "0901234567"
    admin_password: str = "chutiem123"

    turn_timeout_s: float = 120.0


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse file dạng `KEY=VALUE`, bỏ qua dòng trống/comment (#).

    Không xử lý expansion hay quoting phức tạp — đủ dùng cho file .env đơn giản
    của benchmark. Giá trị được strip() hai đầu; dấu `"` hoặc `'` bao quanh
    toàn bộ giá trị (nếu có) sẽ được bỏ đi.
    """
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def load_settings(env_path: Path | None = None) -> BenchSettings:
    """Đọc `bench/.env` (hoặc `env_path` nếu truyền vào) và trả về BenchSettings.

    Chỉ đọc nội dung file — không đụng tới `os.environ`. File không tồn tại
    thì dùng toàn bộ giá trị mặc định.
    """
    path = env_path if env_path is not None else DEFAULT_ENV_PATH
    raw: dict[str, str] = {}
    if path.exists():
        raw = _parse_env_file(path)

    settings = BenchSettings()

    if "BENCH_JUDGE_MODEL" in raw:
        settings.judge_model = raw["BENCH_JUDGE_MODEL"]
    if "BENCH_FOUNDRY_RESOURCE" in raw:
        settings.foundry_resource = raw["BENCH_FOUNDRY_RESOURCE"]
    if "BENCH_FOUNDRY_API_KEY" in raw:
        settings.foundry_api_key = raw["BENCH_FOUNDRY_API_KEY"]
    if "BENCH_BASE_PORT" in raw:
        settings.base_port = int(raw["BENCH_BASE_PORT"])
    if "BENCH_MONGO_URI" in raw:
        settings.mongo_uri = raw["BENCH_MONGO_URI"]
    if "BENCH_LANGFUSE_HOST" in raw:
        settings.langfuse_host = raw["BENCH_LANGFUSE_HOST"]
    if "BENCH_LANGFUSE_PUBLIC_KEY" in raw:
        settings.langfuse_public_key = raw["BENCH_LANGFUSE_PUBLIC_KEY"]
    if "BENCH_LANGFUSE_SECRET_KEY" in raw:
        settings.langfuse_secret_key = raw["BENCH_LANGFUSE_SECRET_KEY"]
    if "BENCH_ADMIN_PHONE" in raw:
        settings.admin_phone = raw["BENCH_ADMIN_PHONE"]
    if "BENCH_ADMIN_PASSWORD" in raw:
        settings.admin_password = raw["BENCH_ADMIN_PASSWORD"]
    if "BENCH_TURN_TIMEOUT_S" in raw:
        settings.turn_timeout_s = float(raw["BENCH_TURN_TIMEOUT_S"])

    return settings
