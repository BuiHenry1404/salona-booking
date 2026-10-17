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

## Chạy benchmark

```bash
# Mọi kịch bản (đọc từ bench/scenarios/), k lần lặp mỗi kịch bản (pass^k):
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --k 4

# Lọc theo tag (GIAO với tập đã chọn, KHÔNG có cú pháp loại trừ — xem dưới):
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --tags edge --k 4

# So hai ref cùng lúc (adapter B chưa tồn tại — xem mục "Thêm adapter B"):
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --ref <branch>:b --k 4
```

Cờ khác: `--no-proxy` (bỏ qua proxy chèn lỗi trước Azure), `--no-judge` (bỏ qua
gọi judge LLM, chạy nhanh hơn khi chỉ cần kiểm DB), `--keep-db` (giữ lại
database Mongo riêng của từng ref sau khi chạy xong, để soi tay).

**`--tags` chỉ lọc kiểu GIAO (OR với tập tag đã cho), không có cú pháp loại
trừ.** Để chạy "mọi kịch bản trừ tag `slow`" (các kịch bản `slow` gọi LLM
nhiều vòng/chờ lâu — ví dụ chờ pending hết hạn 10 phút — nên tách ra chạy
riêng với `--k 1`), liệt kê tường minh các file còn lại qua `--scenarios`:

```bash
# Tạo danh sách kịch bản không có tag slow, rồi chạy:
SLOW='llm/llm-32.yaml edge/pending-het-han.yaml edge/compaction-long.yaml'
FILES=$(find bench/scenarios -name '*.yaml' | \
  grep -vFf <(for s in $SLOW; do echo "bench/scenarios/$s"; done))
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --k 4 --scenarios $FILES

# Rồi chạy riêng các kịch bản slow với k=1:
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --k 1 \
  --scenarios bench/scenarios/llm/llm-32.yaml bench/scenarios/edge/pending-het-han.yaml \
              bench/scenarios/edge/compaction-long.yaml
```

Mỗi lần chạy ghi ra `bench/results/<timestamp>-<runid>/` gồm `meta.json` (cấu
hình + trạng thái từng ref) và `runs/<sha7>/<scenario_id>/<attempt>.json`
(transcript đầy đủ: lượt chat, tool gọi, điểm, judge, token nếu có Langfuse).

## Xem báo cáo

```bash
bench/.venv/bin/python -m bench compare bench/results/<run_dir> -o bench/results/baseline-a.html
# So hai lần chạy (vd hai ref/hai bản) với nhau, lấy một ref làm mốc:
bench/.venv/bin/python -m bench compare bench/results/<run_dir_A> bench/results/<run_dir_B> \
  --base docs/arch-security-review -o bench/results/compare.html
```

Mở file `.html` bằng trình duyệt. Báo cáo gồm: bảng tổng hợp theo ref (pass
rate, pass^k, số lần ghi sai, vi phạm luật văn bản theo từng luật, token nếu
có, điểm judge trung bình theo từng trục), rồi tới bảng chi tiết từng kịch
bản (kịch bản kịch bản hoá vs mô phỏng hiển thị khác nhau — xem hội thoại
từng lượt, tool đã gọi, thay đổi DB). Mục cảnh báo ở đầu báo cáo nêu các vấn
đề về so sánh (hai ref chạy lệch ngày, lệch model, v.v. — `report.warnings`).
Không dán nguyên file HTML vào chat/log — trích số liệu cần thiết bằng tay
hoặc script đọc trực tiếp JSON trong `runs/`.

## Thêm Adapter B (khi branch khung có giao thức xác nhận bằng nút)

Khi nhánh frontend/giao thức mới định nghĩa xong cách xác nhận lịch qua nút
bấm (thay vì gõ "ừ"/"đồng ý"), thêm một adapter mới:

1. Viết `bench/adapters/b.py` theo interface `ChatAdapter` (`bench/adapters/base.py`)
   — cùng cách AdapterA nói chuyện qua Socket.IO, nhưng gửi sự kiện xác nhận
   bằng nút (vd `confirm_booking {"id": ...}`) thay vì gõ văn bản đồng ý.
2. Đăng ký trong `bench/adapters/__init__.py`:
   ```python
   from bench.adapters.b import AdapterB
   ADAPTERS: dict[str, type[ChatAdapter]] = {"a": AdapterA, "b": AdapterB}
   ```
3. Chạy so sánh hai ref/hai giao thức:
   ```bash
   bench/.venv/bin/python -m bench run \
     --ref docs/arch-security-review:a --ref <branch-khung-moi>:b --k 4
   ```
   (`<ref>:a` hoặc không có `:adapter` → mặc định adapter `a`.)
