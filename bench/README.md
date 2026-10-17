# bench — benchmark hộp đen cho salona-booking

Benchmark này đo các bản (ref) của app bằng cách nói chuyện với server **đang
chạy thật** qua HTTP và Socket.IO, và đọc trực tiếp Mongo để kiểm tra kết quả.
Không file nào trong `bench/` được phép `import app` / `from app` — xem
`bench/tests/test_fence.py`.

## Cài đặt

```bash
uv venv bench/.venv --python 3.12
uv pip install -p bench/.venv/bin/python -r bench/requirements.txt
cp bench/.env.example bench/.env   # rồi điền khoá thật
```

## Chạy test

```bash
bench/.venv/bin/python -m pytest -c bench/pytest.ini bench/tests -q
```

(Chạy từ thư mục gốc repo. `pythonpath = .` trong `bench/pytest.ini` giúp
`import bench...` hoạt động mà không cần cài `bench` như package.)

Test cần server thật/Azure thật được đánh dấu `@pytest.mark.live` và bị bỏ
qua mặc định (`addopts = -m "not live"` trong `bench/pytest.ini`).

## Cấu hình

Toàn bộ cấu hình đọc từ `bench/.env` (không đọc biến môi trường shell, cùng
quy ước với `app/config.py` ở repo chính — xem AGENTS.md). Xem
`bench/.env.example` cho danh sách khoá `BENCH_*` và giá trị mặc định tương
ứng trong `bench/config.py`.

## Ràng buộc

- Venv riêng (`bench/.venv`), dependency riêng (`bench/requirements.txt`) —
  không đụng tới `requirements.txt` gốc của repo.
- Mỗi ref server chạy đúng 1 worker uvicorn, cổng riêng bắt đầu từ
  `BENCH_BASE_PORT` (mặc định 8100).
- `bench/.env`, `bench/.venv/`, `bench/results/`, `bench/.worktrees/` không
  commit (xem `.gitignore` gốc).
