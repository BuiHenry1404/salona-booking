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

# So hai ref cùng lúc. Adapter là GIAO THỨC chat, không phải tên ref: nhánh
# feat/langchain-harness giữ giao thức cũ nên dùng `:a` (`:b` chưa tồn tại → lỗi
# "adapter không tồn tại"). Nên dùng tag cố định (vd harness-b/m1) làm --ref
# để mỗi mốc là một commit rõ ràng:
bench/.venv/bin/python -m bench run --ref docs/arch-security-review:a --ref harness-b/m1:a --k 4

# Chạy tiếp một run dở (bị kill, máy tắt...): ref/k/tag lấy từ meta.json, bỏ qua
# mọi <attempt>.json đã có. Muốn chạy lại một attempt thì xoá file của nó trước.
bench/.venv/bin/python -m bench run --resume bench/results/<run_dir>
```

An toàn khi chạy dài / chạy song song:

- Trước khi start server, bench thử bind cổng; cổng đang bị giữ (server mồ côi
  từ run trước, run khác đang chạy) → ref đó `failed_to_start: port busy`.
  Sau khi `/ready` trả 200, bench kiểm lại process của chính nó còn sống.
- Mỗi worktree `bench/.worktrees/<sha7>` có khoá `<sha7>.lock` (flock, tự nhả
  khi process chết): hai run đồng thời không thể dùng chung một worktree/venv/
  `.env`. Muốn chạy hai run song song thì đổi `BENCH_BASE_PORT` cho run thứ hai
  và không đo cùng một commit.
- Server một ref chết giữa run → khởi động lại một lần (cùng DB, cùng cổng,
  ghi `restarts`/`crash_logs` vào meta). Chết lần nữa → `crashed`; báo cáo
  vẫn dùng dữ liệu đã có của ref đó và in cảnh báo.

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

Cột của báo cáo là `ref@sha7`, không phải tên ref: hai run cùng `--ref feat`
nhưng khác commit là hai cột riêng (hiển thị bằng tên ref khi tên đó chỉ có
một commit). `--base` nhận tên ref hoặc `ref@sha7`. Gộp nhiều run của cùng một
commit: pass rate gộp mọi lần chạy, còn pass^k tính riêng từng run trên đúng
`k` lần của run đó rồi lấy trung bình (không bao giờ thành "pass^2k").

Mở file `.html` bằng trình duyệt. Báo cáo gồm: bảng tổng hợp theo ref (pass
rate, pass^k, số lần ghi sai, vi phạm luật văn bản theo từng luật, token nếu
có, điểm judge trung bình theo từng trục), rồi tới bảng chi tiết từng kịch
bản (kịch bản kịch bản hoá vs mô phỏng hiển thị khác nhau — xem hội thoại
từng lượt, tool đã gọi, thay đổi DB). Mục cảnh báo ở đầu báo cáo nêu các vấn
đề về so sánh (hai ref chạy lệch ngày, lệch model, v.v. — `report.warnings`).
Bảng tổng hợp còn có số lần chạy không hợp lệ (n/tổng) theo ref, pass rate
trên tập kịch bản chung của mọi ref, và bảng theo nhóm `edge/llm/fault`. Một
kịch bản mà một ref không hợp lệ ở ≥ nửa số lần chạy còn ref kia thì không bị
đánh dấu "so sánh không công bằng".

"Ghi sai" chỉ tính trên thay đổi DB thật trong lần chạy (lịch mới tạo, lịch bị
dời sai giờ, hủy nhầm, đụng lịch khách khác). Không đạt kỳ vọng mà không ghi
gì sai được ghi riêng ở `score.mismatches`: `missing:` (thiếu lịch kỳ vọng),
`note_mismatch:` (đúng giờ, sai ghi chú), `unchanged:` (lịch có sẵn lẽ ra phải
dời/hủy nhưng không ai đụng).

Không dán nguyên file HTML vào chat/log — trích số liệu cần thiết bằng tay
hoặc script đọc trực tiếp JSON trong `runs/`.

## Chấm lại run đã lưu

```bash
bench/.venv/bin/python -m bench rescore bench/results/<run_dir> [--rejudge]
```

Tính lại `score` mọi lần chạy từ lượt/`before`/`after` đã lưu, theo mã chấm và
`expect` hiện tại; `--rejudge` gọi lại judge (`BENCH_JUDGE_MODEL` của
`bench/.env`). Ghi đè tại chỗ, bản gốc chép vào `<run_dir>/_orig/` ở lần chấm
lại đầu tiên. Chỉ hợp lệ khi kịch bản đổi phần kỳ vọng: nếu setup/lượt khách/
persona đã đổi (so `scenario_fingerprint` trong payload; payload cũ dùng danh
sách `LEGACY_STALE` trong `bench/rescore.py`), lần chạy cũ bị đánh dấu
`stale_scenario`, giữ điểm cũ, và báo cáo loại khỏi tổng hợp — muốn có số cho
kịch bản đó thì phải chạy lại.

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
