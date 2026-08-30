# Định tuyến lại agent chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bỏ hết câu trả lời cứng trong graph chat và đưa mọi ý định của khách về đúng node phụ trách, để AI trả lời tự nhiên như người thay vì đọc thuộc lòng.

**Architecture:** Giữ nguyên 5 node và 3 route, nhưng đổi vai hai node: `status` nở thành `shop` (bận/rảnh + giờ mở cửa), `refuse` bị xoá và thay bằng `social` (chào hỏi, cảm ơn, từ chối — do LLM sinh, không tool). Xưng hô suy ra bằng code từ `full_name` rồi chốt vào khối bối cảnh. Nhánh "khách chưa đồng ý" của `confirm` nối thẳng sang `booking`. Cuối cùng cắt `BOOKING_PROMPT` bằng cách chuyển rule về docstring của tool.

**Tech Stack:** LangGraph + LangChain, FastAPI, Motor/MongoDB, pytest + pytest-asyncio với `FakeModel` monkeypatch (không gọi mạng).

**Spec:** `docs/superpowers/specs/2026-08-30-agent-routing-redesign-design.md`

## Global Constraints

- `VALID_ROUTES` sau khi sửa đúng ba nhãn: `booking`, `shop`, `social`. Nhãn cũ `status` và `refuse` phải biến mất khỏi codebase.
- Nhãn lạ (model trả rác) vẫn fallback về `booking` — hành vi cũ, giữ nguyên.
- `REFUSE_MESSAGE` bị xoá hoàn toàn. Sau Task 4, `grep -rn "REFUSE_MESSAGE" app/ tests/` không được còn kết quả.
- Ràng buộc nghiệp vụ **"chỉ làm tóc và làm nail"** phải sống sót dưới dạng **ví dụ cụ thể bằng tiếng Việt** trong `SOCIAL_PROMPT`, không được viết chung chung.
- Nhánh **đồng ý** của `confirm` giữ nguyên **0 lượt LLM** — đó là nhánh ghi DB, phải tất định. Chỉ nhánh chưa-đồng-ý mới được đi qua LLM.
- **Không tool nào ghi lịch.** `propose_appointment` chỉ giữ chỗ tạm; lịch chỉ ghi ở `confirm` sau khi khách đồng ý ở lượt sau.
- `ShopHours.closed_days` theo quy ước **0 = Chủ Nhật … 6 = Thứ Bảy** (`app/models/shop.py:8`) — **ngược với `datetime.weekday()`** của Python (0 = Thứ Hai).
- Comment và docstring trong repo viết bằng tiếng Việt, giải thích **TẠI SAO** chứ không mô tả code làm gì.
- Giờ đồng hồ luôn viết theo cách người ta đọc: "3 giờ chiều", "9 giờ rưỡi sáng". Không bao giờ viết "15:00".
- Hard rule 2b của `BOOKING_PROMPT` (cấm đụng lịch người khác) **giữ nguyên văn** — đó là rule bảo mật.

## Môi trường

```bash
cd /home/henryb1/Desktop/HenryB1/data/salona-booking
docker compose up -d mongo          # tests cần Mongo thật ở localhost:27017
.venv/bin/python -m pytest -q       # `source .venv/bin/activate` không đưa venv lên PATH ở máy này
```

Task 1 và các bước đo thủ công cần **backend đang chạy ở cổng 8000** và biến môi trường LLM hợp lệ (`llm_provider = azure`, model `gpt-4.1-nano`).

## File Structure

| File | Trách nhiệm | Task |
|---|---|---|
| `scripts/probe_supervisor.py` (create) | In bảng phân loại của supervisor cho một bộ câu mẫu | 1 |
| `scripts/chat_e2e_transcript.py` (modify) | Nâng từ 1 lên 4 kịch bản, chọn bằng `--scenario` | 1 |
| `app/agents/booking_graph/context.py` (modify) | `derive_address`, `address_phrase`, `display_name`, `format_vi_hhmm`, `_clock_phrase`; khối bối cảnh chốt cách gọi | 2, 3 |
| `app/agents/booking_graph/tools.py` (modify) | Bỏ `xung_ho`; `make_status_tools` → `make_shop_tools` + `get_shop_hours`; nhận rule từ prompt về docstring | 2, 3, 6 |
| `app/agents/booking_graph/confirm.py` (modify) | Tự tính xưng hô; nhánh chưa-đồng-ý báo cho graph định tuyến sang `booking` | 2, 5 |
| `app/agents/booking_graph/prompts.py` (modify) | `STATUS_PROMPT`→`SHOP_PROMPT`; thêm `SOCIAL_PROMPT`; xoá `REFUSE_MESSAGE`; sửa `SUPERVISOR_PROMPT`; cắt `BOOKING_PROMPT` | 2, 3, 4, 6 |
| `app/agents/booking_graph/supervisor.py` (modify) | `VALID_ROUTES` 3 nhãn mới; xoá hàm `refuse` | 4 |
| `app/agents/booking_graph/graph.py` (modify) | Node `shop`, node `social`, cạnh `confirm → booking` | 3, 4, 5 |
| `tests/test_context_block.py` (modify) | Test `derive_address` và khối bối cảnh | 2 |
| `tests/test_tools.py` (modify) | Test `get_shop_hours`, test `propose_appointment` hết `xung_ho` | 2, 3 |
| `tests/test_supervisor.py` (modify) | Test 3 route mới, test nhãn cũ đã chết | 4 |
| `tests/test_prompts.py` (modify) | Test `SOCIAL_PROMPT`, xoá test `REFUSE_MESSAGE` | 4, 6 |
| `tests/test_confirm.py` (modify) | Test nhánh chưa-đồng-ý sang `booking` | 5 |
| `tests/test_graph_events.py` (modify) | Test graph có đúng node và cạnh | 3, 4, 5 |

---

### Task 1: Công cụ đo — chạy TRƯỚC khi sửa để có mốc đối chiếu

Task này không theo TDD: sản phẩm là hai script kiểm tay và hai file kết quả làm mốc. Không có gì để assert — mục đích là chụp lại hiện trạng trước khi động vào.

**Files:**
- Create: `scripts/probe_supervisor.py`
- Modify: `scripts/chat_e2e_transcript.py`

**Interfaces:**
- Consumes: `SUPERVISOR_PROMPT` và `build_chat_model` (đã có).
- Produces: `scripts/probe_supervisor.py` chạy được bằng `.venv/bin/python scripts/probe_supervisor.py`; `chat_e2e_transcript.py` nhận thêm `--scenario {booking,doi_y,huy,thong_tin,all}`.

- [ ] **Step 1: Viết `scripts/probe_supervisor.py`**

```python
"""In bảng phân loại của supervisor cho một bộ câu mẫu.

    .venv/bin/python scripts/probe_supervisor.py

Mỗi câu tốn đúng một lượt LLM. Đây là lưới an toàn cho việc định tuyến:
chạy trước khi sửa để có mốc, chạy lại sau khi sửa để so.
"""
import asyncio

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.booking_graph.prompts import SUPERVISOR_PROMPT
from app.agents.llm import build_chat_model

# Cột trái là nhãn MONG ĐỢI sau khi sửa xong. Trước khi sửa sẽ lệch — đó
# chính là thứ cần chụp lại làm mốc.
PROBES = [
    ("booking", "chị muốn làm tóc"),
    ("booking", "mai 3h chiều được không em"),
    ("booking", "chị có lịch lúc nào vậy em"),
    ("booking", "hủy giùm chị lịch mai"),
    ("shop", "tiệm đang bận không em"),
    ("shop", "chừng nào chủ tiệm xong vậy"),
    ("shop", "tiệm mình mở cửa mấy giờ vậy em"),
    ("shop", "mấy giờ tiệm đóng cửa"),
    ("shop", "chủ nhật tiệm có làm không em"),
    ("social", "chào em"),
    ("social", "cảm ơn em nhé"),
    ("social", "thôi chị đi nha"),
    ("social", "cho tôi công thức nấu phở"),
    ("social", "dịch giùm tôi đoạn tiếng Anh này"),
]


async def main() -> None:
    model = build_chat_model(tags=["supervisor"], temperature=0.0)
    sai = 0
    for mong_doi, cau in PROBES:
        reply = await model.ainvoke(
            [SystemMessage(content=SUPERVISOR_PROMPT), HumanMessage(content=cau)]
        )
        thuc_te = (reply.content or "").strip().lower()
        dau = "  " if thuc_te == mong_doi else "✗ "
        if thuc_te != mong_doi:
            sai += 1
        print(f"{dau}{thuc_te:10s} (mong đợi {mong_doi:8s}) <- {cau}")
    print(f"\nlệch {sai}/{len(PROBES)}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Chạy probe, lưu mốc**

```bash
.venv/bin/python scripts/probe_supervisor.py 2>&1 | tee baseline-probe.txt
```

Kết quả mong đợi ở bước này: **lệch nhiều** (giờ mở cửa và xã giao đều rơi vào `refuse`, và `refuse`/`status` là nhãn hiện có nên `social`/`shop` không bao giờ xuất hiện). Đó là mốc.

- [ ] **Step 3: Nâng `chat_e2e_transcript.py` lên 4 kịch bản**

Thay hằng `SCRIPT` bằng dict, và thêm tham số dòng lệnh:

```python
SCENARIOS = {
    # Đặt lịch suôn sẻ — kịch bản kiểm câu xác nhận có đủ ngày/giờ/dịch vụ không.
    "booking": [
        "chào em",
        "chị muốn làm tóc",
        "mai chị rảnh buổi sáng",
        "9 giờ được không em",
        "ừ chốt luôn nha",
        "cảm ơn em nhé",
    ],
    # Đổi ý giữa chừng — đúng ca lượt 6 của transcript cũ, chỗ bot giục khách.
    "doi_y": [
        "chị muốn làm nail mai 9 giờ sáng",
        "à khoan, để chị xem lại",
        "thôi 10 giờ đi em",
        "ừ được đó",
    ],
    # Hủy lịch — kiểm list_my_appointments chạy trước cancel.
    "huy": [
        "chị có lịch nào sắp tới không em",
        "hủy giùm chị cái lịch đó",
        "ừ hủy đi em",
    ],
    # Thông tin tiệm — kịch bản MỚI, hiện đang rơi hết vào refuse.
    "thong_tin": [
        "tiệm mình mở cửa mấy giờ vậy em",
        "chủ nhật có làm không em",
        "tiệm đang bận không em",
        "cho tôi công thức nấu phở",
    ],
}
```

Trong `main()`, thay `args.out` mặc định và thêm:

```python
    parser.add_argument(
        "--scenario", default="all", choices=[*SCENARIOS, "all"],
        help="chạy một kịch bản, hoặc 'all' để chạy hết vào cùng một file",
    )
```

và chọn danh sách câu:

```python
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
```

Vòng lặp ngoài duyệt `names`, vòng trong duyệt câu trong kịch bản đó, và ghi tiêu đề `##### KỊCH BẢN <tên> #####` trước mỗi kịch bản. Giữ nguyên toàn bộ phần ghi `TOOL`/`STREAM`/`BOT` đã có — cột `STREAM` rỗng chính là dấu hiệu câu đó không đi qua LLM, và đó là thứ cần theo dõi.

Giữa hai kịch bản `await asyncio.sleep(3)` để không đụng trần `chat_max_per_hour = 30`.

- [ ] **Step 4: Chạy cả 4 kịch bản, lưu mốc**

```bash
.venv/bin/python scripts/chat_e2e_transcript.py --scenario all --out baseline-transcript.txt
grep -c "STREAM: (rỗng" baseline-transcript.txt
```

Ghi lại con số đếm được — đó là số lượt **không đi qua LLM**. Sau Task 6 con số này phải giảm mạnh (chỉ còn các lượt vào nhánh đồng-ý của `confirm`).

- [ ] **Step 5: Commit**

```bash
git add scripts/probe_supervisor.py scripts/chat_e2e_transcript.py
git commit -m "test: công cụ đo phân loại và transcript 4 kịch bản"
```

Hai file `baseline-*.txt` KHÔNG commit — chúng là kết quả chạy, không phải mã nguồn. Giữ lại trên máy để so sau.

---

### Task 2: Xưng hô suy ra bằng code, bỏ tham số `xung_ho`

**Files:**
- Modify: `app/agents/booking_graph/context.py`
- Modify: `app/agents/booking_graph/tools.py` (`propose_appointment`)
- Modify: `app/agents/booking_graph/confirm.py`
- Modify: `app/agents/booking_graph/prompts.py` (xoá hard rule 6)
- Test: `tests/test_context_block.py`, `tests/test_tools.py`, `tests/test_confirm.py`

**Interfaces:**
- Produces:
  - `derive_address(full_name: Optional[str]) -> tuple[str, str]` — trả `(cách_gọi, tên_đã_bỏ_tiền_tố)`.
  - `address_phrase(full_name: Optional[str]) -> str` — `"chị Lan"` / `"anh Hùng"` / `"anh chị"`.
  - `display_name(full_name: Optional[str]) -> str` — tên hiển thị đã bỏ tiền tố.
  - `propose_appointment(start_at: str, note: Optional[str] = None) -> str` — **hết tham số `xung_ho`**.

**Vì sao hard rule 6 phải xoá NGAY trong task này, không để tới Task 6:** rule 6 bảo model *"always pass `xung_ho`"*. Bỏ tham số khỏi tool mà để rule lại thì model vẫn cố truyền, LangChain nhận kwarg lạ và tool call lỗi. Hai thay đổi này buộc phải đi cùng một commit.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_context_block.py`:

```python
from app.agents.booking_graph.context import (address_phrase, derive_address,
                                              display_name)


class TestDeriveAddress:
    """Tiền tố trong full_name là tín hiệu giới tính DUY NHẤT đang có —
    User model không có trường giới tính. Map nó, đừng vứt đi."""

    def test_co_maps_to_chi(self):
        assert derive_address("Cô Lan") == ("chị", "Lan")

    def test_ba_maps_to_chi(self):
        assert derive_address("Bà Sáu") == ("chị", "Sáu")

    def test_chu_maps_to_anh(self):
        assert derive_address("Chú Hùng") == ("anh", "Hùng")

    def test_ong_maps_to_anh(self):
        assert derive_address("Ông Tư") == ("anh", "Tư")

    def test_bac_is_ambiguous_but_prefix_still_stripped(self):
        # "Bác" không cho biết giới tính, nhưng vẫn phải cắt khỏi tên —
        # prompt cấm tuyệt đối nói "bác".
        assert derive_address("Bác Bảy") == ("anh chị", "Bảy")

    def test_name_without_prefix_falls_back(self):
        assert derive_address("Nguyễn Thị Lan") == ("anh chị", "")

    def test_none_falls_back(self):
        assert derive_address(None) == ("anh chị", "")

    def test_single_word_name_falls_back(self):
        assert derive_address("Lan") == ("anh chị", "")


class TestAddressPhrase:
    def test_gendered_prefix_keeps_the_name(self):
        assert address_phrase("Cô Lan") == "chị Lan"
        assert address_phrase("Chú Hùng") == "anh Hùng"

    def test_ambiguous_drops_the_name(self):
        # "anh chị Bảy" không ai nói. Mơ hồ thì gọi trống.
        assert address_phrase("Bác Bảy") == "anh chị"
        assert address_phrase(None) == "anh chị"


class TestDisplayName:
    def test_prefix_is_stripped(self):
        assert display_name("Cô Lan") == "Lan"
        assert display_name("Bác Bảy") == "Bảy"

    def test_name_without_prefix_kept_whole(self):
        assert display_name("Nguyễn Thị Lan") == "Nguyễn Thị Lan"

    def test_missing_name(self):
        assert display_name(None) == "khách"
```

Và test khối bối cảnh, cùng file:

```python
class TestContextBlockAddress:
    def _block(self, full_name):
        from app.models.shop import ShopStatusView
        from app.models.user import User
        from app.agents.booking_graph.context import build_context_block
        user = User(phone="0912345678", hashed_password="x", full_name=full_name)
        return build_context_block(user, ShopStatusView(is_busy=False), [])

    def test_block_pins_one_form_of_address(self):
        # Model chỉ việc chép dòng này, không tự chọn register mỗi lượt.
        assert "Gọi khách là: chị Lan" in self._block("Cô Lan")

    def test_block_never_leaks_the_forbidden_register(self):
        # BOOKING_PROMPT cấm nói "cô/chú/bác"; khối bối cảnh không được
        # đưa chính những chữ đó vào miệng model.
        block = self._block("Bác Bảy")
        for cam in ("Cô ", "Chú ", "Bác "):
            assert cam not in block
```

Thêm vào `tests/test_tools.py`:

```python
async def test_propose_appointment_has_no_xung_ho_parameter(test_db):
    """Xưng hô giờ suy ra bằng code từ full_name — model không cần truyền,
    và không được phép truyền (kwarg lạ làm tool call lỗi)."""
    from app.agents.booking_graph.tools import make_booking_tools
    from app.models.user import User

    user = User(phone="0912345678", hashed_password="x", full_name="Cô Lan")
    tools = {t.name: t for t in make_booking_tools(test_db, user)}
    assert "xung_ho" not in tools["propose_appointment"].args
```

- [ ] **Step 2: Chạy test cho chắc là fail**

Run: `.venv/bin/python -m pytest tests/test_context_block.py tests/test_tools.py -q`
Expected: FAIL — `ImportError: cannot import name 'derive_address'`, và test `xung_ho` fail vì tham số vẫn còn.

- [ ] **Step 3: Thêm ba hàm vào `context.py`**

Đặt ngay trên `build_context_block`:

```python
# Tiền tố xưng hô nào cũng phải CẮT khỏi tên (prompt cấm nói cô/chú/bác),
# nhưng chỉ một số cho biết giới tính để suy ra "anh" hay "chị".
_HONORIFICS = {"cô", "chị", "bà", "chú", "anh", "ông", "bác", "em"}
_GENDERED = {
    "cô": "chị", "chị": "chị", "bà": "chị",
    "chú": "anh", "anh": "anh", "ông": "anh",
}


def derive_address(full_name: Optional[str]) -> tuple[str, str]:
    """Suy cách gọi khách từ tên. Trả (cách_gọi, tên_đã_bỏ_tiền_tố).

    13/13 khách trong DB có full_name mang sẵn tiền tố ("Cô Lan", "Bác Bảy").
    Đó là tín hiệu giới tính DUY NHẤT đang có — User model không có trường
    giới tính — nên map nó thay vì vứt đi. "Bác" không cho biết giới nên lùi
    về "anh chị", trùng với fallback vốn có ở confirm.py.
    """
    parts = (full_name or "").split()
    if len(parts) >= 2 and parts[0].lower() in _HONORIFICS:
        return _GENDERED.get(parts[0].lower(), "anh chị"), " ".join(parts[1:])
    return "anh chị", ""


def address_phrase(full_name: Optional[str]) -> str:
    """'chị Lan', 'anh Hùng', hoặc 'anh chị' khi không đoán được giới tính.

    Không ghép tên vào lời gọi trống: "anh chị Bảy" không ai nói.
    """
    cach_goi, ten = derive_address(full_name)
    return f"{cach_goi} {ten}" if ten and cach_goi != "anh chị" else cach_goi


def display_name(full_name: Optional[str]) -> str:
    """Tên để hiển thị trong khối bối cảnh, đã bỏ tiền tố xưng hô."""
    _, ten = derive_address(full_name)
    return ten or full_name or "khách"
```

- [ ] **Step 4: Chốt cách gọi vào khối bối cảnh**

Trong `build_context_block`, thay dòng danh tính:

```python
        f"Bạn đang nói chuyện với: {display_name(user.full_name)} ({user.phone}).",
        # Chốt MỘT cách gọi cho cả cuộc. Không có dòng này thì model tự chọn
        # lại mỗi lượt: transcript đã thấy cùng một khách bị gọi "chú Hùng"
        # ở lượt 3 rồi "chị" ở lượt 5.
        f"Gọi khách là: {address_phrase(user.full_name)}.",
```

- [ ] **Step 5: Bỏ `xung_ho` khỏi `propose_appointment`**

Trong `app/agents/booking_graph/tools.py`, đổi chữ ký và docstring:

```python
    @tool
    async def propose_appointment(start_at: str, note: Optional[str] = None) -> str:
        """Giữ chỗ tạm thời và chuẩn bị câu hỏi xác nhận cho khách.
        `start_at` dạng ISO 8601, lấy NGUYÊN từ kết quả parse_time.
        Gọi tool này rồi hỏi khách xác nhận. KHÔNG có tool nào ghi lịch trực tiếp —
        lịch chỉ được ghi khi khách trả lời đồng ý ở lượt sau."""
```

và bỏ `xung_ho` khỏi payload lưu pending:

```python
        await conversations.set_pending(
            str(user.id), {"start_at": start.isoformat(), "note": note},
        )
```

- [ ] **Step 6: `confirm.py` tự tính xưng hô**

Thêm import:

```python
from app.agents.booking_graph.context import address_phrase, format_vi_datetime
```

Thay khối tính xưng hô (đang đọc `pending.get("xung_ho")`):

```python
        # Suy từ full_name bằng code, không nhận từ model nữa: model quên
        # truyền là câu chốt mất lời gọi, mà nó quên thật — đó là lý do
        # hard rule 6 từng tồn tại.
        goi = address_phrase(user.full_name)
```

Và bỏ biến `loi_goi`, dùng thẳng `goi` ở câu chốt:

```python
        return {"answer": f"Xong rồi ạ. Hẹn gặp {goi} "
                          f"{format_vi_datetime(appointment.start_at)} nhé."}
```

- [ ] **Step 7: Xoá hard rule 6 khỏi `BOOKING_PROMPT`**

Xoá trọn khối rule 6 trong `app/agents/booking_graph/prompts.py` (bắt đầu bằng `6. When you call propose_appointment, always pass \`xung_ho\``). Không đánh số lại các rule còn lại ở task này — Task 6 sẽ viết lại cả prompt.

- [ ] **Step 8: Chạy test cho chắc là pass**

Run: `.venv/bin/python -m pytest tests/test_context_block.py tests/test_tools.py tests/test_confirm.py -q`
Expected: PASS

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — không failure mới. Nếu `tests/test_confirm.py` có test cũ dựng pending kèm `xung_ho`, sửa test đó bỏ khoá `xung_ho` đi (payload không còn khoá này).

- [ ] **Step 9: Commit**

```bash
git add app/agents/booking_graph/context.py app/agents/booking_graph/tools.py \
        app/agents/booking_graph/confirm.py app/agents/booking_graph/prompts.py tests/
git commit -m "fix: suy xưng hô bằng code từ full_name, bỏ tham số xung_ho"
```

---

### Task 3: Node `shop` — đổi tên từ `status`, thêm `get_shop_hours`

**Files:**
- Modify: `app/agents/booking_graph/context.py` (tách `_clock_phrase`, thêm `format_vi_hhmm`)
- Modify: `app/agents/booking_graph/tools.py` (`make_status_tools` → `make_shop_tools`)
- Modify: `app/agents/booking_graph/prompts.py` (`STATUS_PROMPT` → `SHOP_PROMPT`)
- Modify: `app/agents/booking_graph/graph.py`
- Test: `tests/test_tools.py`, `tests/test_context_block.py`

**Interfaces:**
- Consumes: `ShopService.get_hours() -> ShopHours` (`open_time: str`, `close_time: str`, `closed_days: List[int]`).
- Produces:
  - `format_vi_hhmm(hhmm: str) -> str` — `"08:00"` → `"8 giờ sáng"`.
  - `make_shop_tools(db, user) -> List[BaseTool]` — đúng 2 tool: `get_shop_status`, `get_shop_hours`.
  - `SHOP_PROMPT`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_context_block.py`:

```python
from app.agents.booking_graph.context import format_vi_hhmm


class TestFormatVietnameseHhmm:
    """Giờ mở cửa lưu dạng chuỗi 'HH:MM', không phải datetime — nhưng đọc
    lên vẫn phải nghe như người nói, không phải '08:00'."""

    def test_morning(self):
        assert format_vi_hhmm("08:00") == "8 giờ sáng"

    def test_afternoon(self):
        assert format_vi_hhmm("14:00") == "2 giờ chiều"

    def test_noon_is_afternoon(self):
        assert format_vi_hhmm("12:00") == "12 giờ chiều"

    def test_evening(self):
        assert format_vi_hhmm("19:00") == "7 giờ tối"

    def test_half_past_reads_as_ruoi(self):
        assert format_vi_hhmm("09:30") == "9 giờ rưỡi sáng"

    def test_odd_minutes(self):
        assert format_vi_hhmm("13:45") == "1 giờ 45 chiều"
```

Thêm vào `tests/test_tools.py`. File này đã có `pytestmark = pytest.mark.asyncio`
ở cấp module, nên method async trong class chạy được mà không cần decorator.
**Đồng thời phải sửa dòng import đầu file** — nó đang import `make_status_tools`,
tên đó biến mất ở bước sau:

```python
from app.agents.booking_graph.tools import make_booking_tools, make_shop_tools
```

```python
class TestShopHoursTool:
    """closed_days theo quy ước 0 = Chủ Nhật … 6 = Thứ Bảy
    (app/models/shop.py). NGƯỢC với datetime.weekday() của Python
    (0 = Thứ Hai) — đây là chỗ dễ lệch nhất trong cả tính năng."""

    async def _call(self, test_db, open_time, close_time, closed_days):
        from app.agents.booking_graph.tools import make_shop_tools
        from app.models.user import User
        from app.services.shop import ShopService

        await ShopService(test_db).set_hours(open_time, close_time, closed_days)
        user = User(phone="0912345678", hashed_password="x", full_name="Cô Lan")
        tools = {t.name: t for t in make_shop_tools(test_db, user)}
        return await tools["get_shop_hours"].ainvoke({})

    async def test_shop_tools_has_exactly_two_tools(self, test_db):
        from app.agents.booking_graph.tools import make_shop_tools
        from app.models.user import User

        user = User(phone="0912345678", hashed_password="x")
        names = {t.name for t in make_shop_tools(test_db, user)}
        assert names == {"get_shop_status", "get_shop_hours"}

    async def test_open_and_close_are_spoken_not_digits(self, test_db):
        out = await self._call(test_db, "08:00", "19:00", [])
        assert "8 giờ sáng" in out
        assert "7 giờ tối" in out
        assert "08:00" not in out

    async def test_zero_means_sunday_not_monday(self, test_db):
        out = await self._call(test_db, "08:00", "19:00", [0])
        assert "Chủ Nhật" in out
        assert "Thứ Hai" not in out

    async def test_six_means_saturday(self, test_db):
        out = await self._call(test_db, "08:00", "19:00", [6])
        assert "Thứ Bảy" in out

    async def test_no_closed_days_says_open_all_week(self, test_db):
        out = await self._call(test_db, "08:00", "19:00", [])
        assert "cả tuần" in out
```

- [ ] **Step 2: Chạy test cho chắc là fail**

Run: `.venv/bin/python -m pytest tests/test_context_block.py tests/test_tools.py -q -k "Hhmm or ShopHours"`
Expected: FAIL — `cannot import name 'format_vi_hhmm'` và `cannot import name 'make_shop_tools'`.

- [ ] **Step 3: Tách `_clock_phrase` rồi thêm `format_vi_hhmm`**

Trong `context.py`, rút phần tính giờ đang nằm trong `format_vi_datetime` ra thành hàm dùng chung (tránh chép nguyên khối logic sang hàm thứ hai):

```python
def _clock_phrase(hour: int, minute: int) -> str:
    """'3 giờ chiều', '9 giờ rưỡi sáng', '1 giờ 45 chiều'.

    Dùng chung cho cả datetime lẫn chuỗi 'HH:MM' — hai chỗ mà lệch nhau một
    chữ là khách nghe ra hai giọng khác nhau trong cùng một cuộc.
    """
    if hour < 12:
        period, display = "sáng", hour
    elif hour < 18:
        period, display = "chiều", hour - 12 if hour > 12 else 12
    else:
        period, display = "tối", hour - 12

    if minute == 0:
        clock = f"{display} giờ"
    elif minute == 30:
        clock = f"{display} giờ rưỡi"      # không ai đọc "9 giờ 30"
    else:
        clock = f"{display} giờ {minute:02d}"
    return f"{clock} {period}"


def format_vi_hhmm(hhmm: str) -> str:
    """'08:00' -> '8 giờ sáng'. Giờ mở cửa lưu dạng chuỗi, không phải datetime."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    return _clock_phrase(hour, minute)
```

Rồi rút gọn `format_vi_datetime` cho dùng lại:

```python
    local = to_local(dt)
    return (f"{_WEEKDAYS[local.weekday()]} {local.day}/{local.month}, "
            f"{_clock_phrase(local.hour, local.minute)}")
```

- [ ] **Step 4: Đổi `make_status_tools` thành `make_shop_tools` và thêm tool**

Trong `tools.py`, thêm import `format_vi_hhmm` cạnh `format_vi_datetime`, thêm hằng ngay trên hàm:

```python
# Index CHÍNH LÀ giá trị trong closed_days: 0 = Chủ Nhật (app/models/shop.py).
# Ngược với datetime.weekday() của Python, nên tuyệt đối không dùng _WEEKDAYS.
_CLOSED_DAY_NAMES = [
    "Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy",
]
```

Đổi tên hàm và thêm tool thứ hai:

```python
def make_shop_tools(db: AsyncIOMotorDatabase, user: User) -> List[BaseTool]:
```

```python
    @tool
    async def get_shop_hours() -> str:
        """Giờ mở cửa và ngày nghỉ của tiệm. Gọi khi khách hỏi tiệm mở mấy giờ,
        đóng mấy giờ, hay có làm ngày nào đó không."""
        hours = await ShopService(db).get_hours()
        nghi = [_CLOSED_DAY_NAMES[d] for d in sorted(hours.closed_days)
                if 0 <= d < len(_CLOSED_DAY_NAMES)]
        lich_nghi = f"Nghỉ {', '.join(nghi)}." if nghi else "Mở cả tuần."
        return (
            f"Tiệm mở từ {format_vi_hhmm(hours.open_time)} "
            f"đến {format_vi_hhmm(hours.close_time)}. {lich_nghi}"
        )
```

và trả về cả hai:

```python
    return [get_shop_status, get_shop_hours]
```

- [ ] **Step 5: `STATUS_PROMPT` → `SHOP_PROMPT`**

Đổi tên hằng trong `prompts.py`, sửa dòng mở đầu và thêm luật cho tool mới. Giữ nguyên toàn bộ hard rule cũ (mốc giờ tuyệt đối, cấm đếm ngược, cách viết giờ):

```
Call get_shop_status when the customer asks whether the owner is busy or free.
Call get_shop_hours when they ask what time the salon opens or closes, or
whether it is open on a given day. Answer with what the tool returned — never
invent opening hours.
```

- [ ] **Step 6: Đấu lại trong `graph.py`**

Đổi import `make_status_tools` → `make_shop_tools`, `STATUS_PROMPT` → `SHOP_PROMPT`, và đổi tên node:

```python
    graph.add_node(
        "shop",
        make_subagent_node(SHOP_PROMPT, make_shop_tools(db, user), tag=RESPOND_TAG),
    )
```

Trong `add_conditional_edges` của supervisor đổi `"status": "status"` thành `"shop": "shop"`, và trong vòng `for node in (...)` đổi `"status"` thành `"shop"`.

- [ ] **Step 7: Chạy test cho chắc là pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Test nào còn nhắc `make_status_tools` / `STATUS_PROMPT` / route `"status"` thì sửa sang tên mới — đây là đổi tên nội bộ, không có gì ngoài `app/agents/` phụ thuộc.

- [ ] **Step 8: Commit**

```bash
git add app/agents/booking_graph/ tests/
git commit -m "feat: node shop gánh cả giờ mở cửa, thay cho node status"
```

---

### Task 4: Node `social` thay `refuse`, supervisor đổi bộ nhãn

**Files:**
- Modify: `app/agents/booking_graph/prompts.py` (thêm `SOCIAL_PROMPT`, xoá `REFUSE_MESSAGE`, sửa `SUPERVISOR_PROMPT`)
- Modify: `app/agents/booking_graph/supervisor.py` (xoá hàm `refuse`, sửa `VALID_ROUTES`)
- Modify: `app/agents/booking_graph/graph.py`
- Test: `tests/test_supervisor.py`, `tests/test_prompts.py`

**Interfaces:**
- Consumes: `make_subagent_node(prompt, tools, tag)` — gọi với `tools=[]`.
- Produces: `SOCIAL_PROMPT`; `VALID_ROUTES == {"booking", "shop", "social"}`.

- [ ] **Step 1: Viết test thất bại**

Trong `tests/test_supervisor.py`, thay test `test_off_topic_routes_to_refuse` và thêm:

```python
@pytest.mark.asyncio
async def test_small_talk_routes_to_social(patch_model):
    patch_model("social")
    assert (await supervise(a_state("chào em")))["route"] == "social"


@pytest.mark.asyncio
async def test_shop_question_routes_to_shop(patch_model):
    patch_model("shop")
    assert (await supervise(a_state("tiệm mở cửa mấy giờ")))["route"] == "shop"


@pytest.mark.asyncio
async def test_the_retired_labels_are_no_longer_valid(patch_model):
    """`status` và `refuse` đã chết. Nếu model lỡ trả nhãn cũ mà ta coi là
    hợp lệ, LangGraph sẽ nổ lúc chạy vì không có node tên đó — phải rơi về
    booking như mọi nhãn lạ khác."""
    for nhan_cu in ("status", "refuse"):
        patch_model(nhan_cu)
        assert (await supervise(a_state("gì đó")))["route"] == "booking"


def test_refuse_node_is_gone():
    import app.agents.booking_graph.supervisor as sup
    assert not hasattr(sup, "refuse")
```

Sửa import ở đầu file, bỏ `refuse`:

```python
from app.agents.booking_graph.supervisor import route_from_state, supervise
```

Trong `tests/test_prompts.py`, xoá ba assert về `REFUSE_MESSAGE` (dòng 30, 142, 143) và bộ nhãn ở dòng 86, thay bằng:

```python
    def test_supervisor_lists_exactly_the_three_live_labels(self):
        for label in ("booking", "shop", "social"):
            assert label in SUPERVISOR_PROMPT
        for retired in ("status", "refuse"):
            assert retired not in SUPERVISOR_PROMPT

    def test_social_prompt_carries_the_scope_example_verbatim(self):
        """Luật chung chung bị model bỏ qua; chỉ ăn khi có ví dụ — cùng lý do
        với các luật khác trong class này. REFUSE_MESSAGE cũ chốt phạm vi
        'chỉ tóc và nail'; ràng buộc đó phải sống tiếp ở đây."""
        assert "chỉ lo đặt lịch làm tóc với làm nail" in SOCIAL_PROMPT

    def test_social_prompt_is_vietnamese_and_polite(self):
        assert "em" in SOCIAL_PROMPT
        assert "cô chú" not in SOCIAL_PROMPT
```

Sửa import trong `tests/test_prompts.py`: bỏ `REFUSE_MESSAGE`, thêm `SOCIAL_PROMPT` và `SHOP_PROMPT`.

- [ ] **Step 2: Chạy test cho chắc là fail**

Run: `.venv/bin/python -m pytest tests/test_supervisor.py tests/test_prompts.py -q`
Expected: FAIL — `cannot import name 'SOCIAL_PROMPT'`, và `test_refuse_node_is_gone` fail vì hàm còn đó.

- [ ] **Step 3: Sửa `SUPERVISOR_PROMPT` và thêm `SOCIAL_PROMPT`**

Thay khối nhãn trong `SUPERVISOR_PROMPT`:

```
- booking : book, change, or cancel an appointment; ask for free slots; look up
            their own appointments
- shop    : whether the owner is busy or free, when they finish, what time the
            salon opens or closes, which days it is closed
- social  : everything else — greetings, thanks, goodbyes, small talk, and
            requests outside the salon's business

If torn between booking and social, output booking.
```

Thêm `SOCIAL_PROMPT` (thay chỗ `REFUSE_MESSAGE`, xoá hằng cũ đi):

```python
SOCIAL_PROMPT = f"""You are the receptionist at a Vietnamese nail and hair
salon. This turn is NOT about an appointment.

{_VIETNAMESE_ONLY}

You have no tools. Answer from this prompt alone.

HARD RULES:

1. Write ONE short reply — one or two sentences. Never repeat a sentence.

2. A greeting at the start of a chat and a thank-you at the end are DIFFERENT
   situations. Never answer both with the same sentence.
   - "chào em" -> greet back, then ask what they need.
   - "cảm ơn em nhé" / "chị đi nha" -> accept the thanks warmly and say
     goodbye. Do NOT push them to book again — they are leaving.

3. If they ask for something outside the salon's business (general knowledge,
   translation, advice, ads), decline in ONE sentence then steer back. Say it
   like this:
   "Dạ em chỉ lo đặt lịch làm tóc với làm nail thôi ạ. Anh chị cần đặt ngày
   nào để em xem giúp ạ?"
   Never explain why you cannot, never apologise at length, never argue.

4. Never invent salon facts — prices, addresses, services beyond hair and
   nails. You do not know them. If asked, say you will let the owner answer.

VOICE: call yourself "em"; address the customer exactly as the "Gọi khách là"
line in the context block says; short sentences; no technical terms; no bullet
points. Never call yourself "con" and never say "cô", "chú" or "bác" — that is
a different register and does not go with "anh"/"chị".

{_VIETNAMESE_ONLY}"""
```

- [ ] **Step 4: Xoá hàm `refuse`**

Trong `supervisor.py`: xoá hàm `refuse`, bỏ `REFUSE_MESSAGE` khỏi import, và sửa hằng:

```python
VALID_ROUTES = {"booking", "shop", "social"}
```

- [ ] **Step 5: Đấu `social` vào graph**

Trong `graph.py`: bỏ import `refuse`, thêm import `SOCIAL_PROMPT`, thay node `refuse` bằng:

```python
    # Không tool: xã giao và từ chối không cần dữ liệu gì. Vẫn dựng qua
    # make_subagent_node để token stream ra màn hình như các node khác —
    # trả thẳng chuỗi chính là cái bẫy mà node refuse cũ mắc phải.
    graph.add_node(
        "social", make_subagent_node(SOCIAL_PROMPT, [], tag=RESPOND_TAG)
    )
```

Sửa map định tuyến thành `{"booking": "booking", "shop": "shop", "social": "social"}`, và vòng `for node in ("confirm", "social", "shop", "booking")`.

- [ ] **Step 6: Chạy test cho chắc là pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

Run: `grep -rn "REFUSE_MESSAGE\|def refuse" app/ tests/`
Expected: không có kết quả nào.

- [ ] **Step 7: Đo lại phân loại**

```bash
.venv/bin/python scripts/probe_supervisor.py
```

Expected: lệch **0/14**, hoặc nếu còn lệch thì ghi lại câu nào lệch và báo cáo — đừng sửa prompt vòng vo quá hai lần, báo lên để quyết.

- [ ] **Step 8: Commit**

```bash
git add app/agents/booking_graph/ tests/
git commit -m "feat: node social thay refuse, bỏ hẳn câu trả lời cứng"
```

---

### Task 5: Cạnh `confirm → booking` khi khách chưa đồng ý

**Files:**
- Modify: `app/agents/booking_graph/confirm.py`
- Modify: `app/agents/booking_graph/graph.py`
- Test: `tests/test_confirm.py`, `tests/test_graph_events.py`

**Interfaces:**
- Consumes: `is_affirmative(text) -> bool` (đã có trong `confirm.py`).
- Produces: node `confirm` trả `{"route": "booking"}` khi khách chưa đồng ý (không kèm `answer`); hàm định tuyến `route_after_confirm(state) -> str` trong `confirm.py`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_confirm.py`:

```python
class TestNotAffirmativeGoesToBooking:
    """Khách nói "khoan để chị xem lại" thì vẫn đang đặt lịch — đẩy sang
    booking để nó trả lời bằng lịch sử và đủ tool, thay vì đọc một câu cứng
    giục khách chọn giờ."""

    @pytest.mark.asyncio
    async def test_hesitation_does_not_write_an_appointment(self, test_db):
        from app.agents.booking_graph.confirm import make_confirm_node
        from app.services.auth import AuthService

        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        node = make_confirm_node(test_db, user)
        state = {
            "messages": [HumanMessage(content="à khoan, để chị xem lại")],
            "user_id": str(user.id),
            "pending_confirmation": {"start_at": "2026-09-01T02:00:00+00:00", "note": "làm tóc"},
        }

        result = await node(state)

        assert result.get("route") == "booking"
        assert "answer" not in result
        assert await test_db["appointments"].count_documents({}) == 0

    @pytest.mark.asyncio
    async def test_affirmative_still_writes_and_answers_without_llm(self, test_db):
        from app.agents.booking_graph.confirm import make_confirm_node
        from app.services.auth import AuthService

        user = await AuthService(test_db).create_user("0912345678", "matkhau123", "Cô Lan")
        node = make_confirm_node(test_db, user)
        start = (datetime.now(TZ) + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        state = {
            "messages": [HumanMessage(content="ừ chốt luôn nha")],
            "user_id": str(user.id),
            "pending_confirmation": {"start_at": start.isoformat(), "note": "làm tóc"},
        }

        result = await node(state)

        assert "Xong rồi ạ" in result["answer"]
        assert "chị Lan" in result["answer"]
        assert result.get("route") is None
        assert await test_db["appointments"].count_documents({}) == 1
```

Bổ sung import ở đầu file test nếu thiếu: `from datetime import datetime, timedelta`, `from app.core.clock import TZ`, `from langchain_core.messages import HumanMessage`.

Thêm vào `tests/test_graph_events.py` (file này hiện toàn test đồng bộ và
không import `pytest` — phải thêm `import pytest` ở đầu file):

```python
@pytest.mark.asyncio
async def test_graph_has_the_three_live_routes_and_no_refuse(test_db):
    """Nếu graph thiếu node mà supervisor trả nhãn đó, LangGraph nổ lúc chạy
    chứ không phải lúc test — nên chốt danh sách node ở đây.

    Phải truyền `test_db` thật, KHÔNG truyền None: BaseRepository.__init__
    làm `db[collection_name]` ngay lúc dựng, nên None nổ TypeError.
    """
    from app.agents.booking_graph.graph import build_graph
    from app.models.user import User

    user = User(phone="0912345678", hashed_password="x", full_name="Cô Lan")
    nodes = set(build_graph(test_db, user).get_graph().nodes)
    for expected in ("supervisor", "booking", "shop", "social", "confirm"):
        assert expected in nodes
    assert "refuse" not in nodes
```

- [ ] **Step 2: Chạy test cho chắc là fail**

Run: `.venv/bin/python -m pytest tests/test_confirm.py tests/test_graph_events.py -q`
Expected: FAIL — node hiện trả `{"answer": ...}` chứ không trả `{"route": "booking"}`.

- [ ] **Step 3: Sửa nhánh chưa-đồng-ý trong `confirm.py`**

Thay:

```python
        if not is_affirmative(last_message):
            return {"answer": f"Dạ vâng, vậy {goi} muốn đặt ngày giờ nào ạ?"}
```

bằng:

```python
        if not is_affirmative(last_message):
            # Khách chưa chốt thì VẪN đang đặt lịch — "khoan để chị xem lại",
            # "thôi 10 giờ đi em", "đổi sang thứ Năm" đều là chuyện của
            # booking. Node này không có LLM nên trả lời cứng chỗ nào cũng
            # trật; đẩy sang agent có lịch sử và đủ tool.
            return {"route": "booking"}
```

Thêm hàm định tuyến ở cuối file:

```python
def route_after_confirm(state: GraphState) -> str:
    """Sau confirm: đã ghi lịch (có `answer`) thì xong; chưa chốt thì sang booking."""
    return "booking" if state.get("route") == "booking" else "end"
```

- [ ] **Step 4: Đấu cạnh trong `graph.py`**

Bỏ `"confirm"` khỏi vòng `for node in (...)` nối thẳng tới `END`, thay bằng cạnh có điều kiện:

```python
    graph.add_conditional_edges(
        "confirm", route_after_confirm, {"booking": "booking", "end": END}
    )

    for node in ("social", "shop", "booking"):
        graph.add_edge(node, END)
```

Thêm import `route_after_confirm` từ `app.agents.booking_graph.confirm`.

- [ ] **Step 5: Chạy test cho chắc là pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Test cũ nào assert câu `"Dạ vâng, vậy ... muốn đặt ngày giờ nào ạ?"` phải xoá — câu đó không còn tồn tại.

- [ ] **Step 6: Kiểm bằng kịch bản thật**

```bash
.venv/bin/python scripts/chat_e2e_transcript.py --scenario doi_y --out after-doi-y.txt
cat after-doi-y.txt
```

Expected: lượt "à khoan, để chị xem lại" **không** còn bị giục chọn giờ, và cột `STREAM` của lượt đó **không** còn rỗng.

- [ ] **Step 7: Commit**

```bash
git add app/agents/booking_graph/ tests/
git commit -m "feat: khách chưa chốt thì confirm chuyển tiếp sang booking"
```

---

### Task 6: Cắt `BOOKING_PROMPT` — commit riêng, cuối cùng

Đây là task duy nhất có rủi ro làm tụt chất lượng mà **không có test tự động nào bắt được**. Gom trọn vào một commit để revert được một mình.

**Files:**
- Modify: `app/agents/booking_graph/prompts.py`
- Modify: `app/agents/booking_graph/tools.py` (docstring nhận rule về)
- Test: `tests/test_prompts.py`

**Interfaces:**
- Không đổi chữ ký hàm nào. Chỉ chuyển chữ giữa prompt và docstring.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_prompts.py`:

```python
class TestBookingPromptSlimmed:
    def test_the_security_rule_survives_verbatim(self):
        """Rule 2b là rule BẢO MẬT và nó bảo model ĐỪNG gọi tool nào cả —
        docstring của tool là chỗ sai để nói điều đó. Phải ở lại prompt."""
        assert "Dạ em chỉ xem và đặt lịch cho chính anh chị thôi ạ" in BOOKING_PROMPT
        assert "NEVER act on anyone else's appointments" in BOOKING_PROMPT

    def test_the_confirmation_sentence_is_no_longer_a_fixed_template(self):
        """Khuôn cứng làm mọi lượt xác nhận ra một câu như nhau. Ràng buộc
        phải là NỘI DUNG (đủ ngày, giờ, dịch vụ, có hỏi lại), không phải chữ."""
        assert "Em đặt Thứ Năm 7/8, 3 giờ chiều, làm tóc — đúng không chị?" not in BOOKING_PROMPT

    def test_the_prompt_actually_got_shorter(self):
        # Trước khi cắt: 4718 ký tự. Cắt xong phải dưới 3000 — nếu không thì
        # rule chưa thực sự chuyển đi đâu cả.
        assert len(BOOKING_PROMPT) < 3000

    def test_picking_a_day_unasked_is_forbidden(self):
        """Transcript cũ: khách mới nói "chị muốn làm tóc", bot đã chào giờ
        trống HÔM NAY. Chỉ được tự chọn ngày khi khách nói rõ là tùy tiệm."""
        assert "only when the customer says the salon may choose" in BOOKING_PROMPT
```

- [ ] **Step 2: Chạy test cho chắc là fail**

Run: `.venv/bin/python -m pytest tests/test_prompts.py -q -k Slimmed`
Expected: FAIL — prompt vẫn còn khuôn câu cứng và vẫn dài hơn 3000 ký tự.

- [ ] **Step 3: Chuyển rule về docstring của tool**

`parse_time` — nhận rule 5, thêm vào cuối docstring hiện có:

```
        Gọi tool này TRƯỚC find_free_slots và propose_appointment, mỗi khi
        khách nhắc tới thời gian. Không tự tính ngày.
        Kết quả có `start_at` -> truyền NGUYÊN chuỗi đó sang propose_appointment,
        không sửa, không diễn giải lại, không gõ lại.
        Kết quả có `missing` -> hỏi khách đúng MỘT mảnh còn thiếu đó, mỗi lượt
        một mảnh. Thiếu ["sáng hay chiều"] thì hỏi "Dạ 3 giờ chiều hay 3 giờ
        sáng ạ chị?" và không hỏi gì thêm.
```

`find_free_slots` — nhận rule 3 và 4:

```
        Chỉ dùng kết quả của tool này, TUYỆT ĐỐI không bịa giờ trống.
        Giờ khách xin đã có người: mời hai mốc trống gần giờ đó nhất.
        Khách nói rõ là tùy tiệm ("lúc nào vắng thì xếp em", "khi nào rảnh
        cũng được") thì gọi cho hôm nay — hoặc mai nếu hôm nay đã hết giờ —
        rồi mời hai ba mốc. Khách CHƯA nói ngày thì hỏi ngày trước, đừng tự
        chọn hôm nay.
```

`list_my_appointments` và `cancel_appointment` — rule 2 đã có sẵn phần lớn, bổ sung cho đủ:

```
        Khách hỏi về lịch của chính họ ("chị có lịch lúc nào", "xem giùm em")
        thì gọi tool này NGAY, đừng hỏi ngày trước — tool tự lọc theo khách
        đang đăng nhập. Không có lịch nào thì nói thẳng.
```

- [ ] **Step 4: Viết lại `BOOKING_PROMPT`**

Giữ lại: câu mở đầu, `_VIETNAMESE_ONLY`, rule 2b **nguyên văn**, các rule về cách nói (một câu trả lời mỗi lượt, không lặp câu, cách viết giờ), khối VOICE. Thay rule 1 bằng ràng buộc nội dung:

```
HARD RULES:

1. You have NO tool that writes an appointment. Required order:
   parse_time -> find_free_slots (if needed) -> propose_appointment -> ask the
   customer to confirm. The appointment is written only when the customer
   agrees on the NEXT turn. Never say it is already booked before that.

2. After propose_appointment succeeds, read the booking back to the customer
   and ask them to confirm. Your sentence MUST contain the weekday and date,
   the clock time, and the service, and MUST end in a question.
   Vary the wording — a customer who books twice should not hear the same
   sentence twice. What is fixed is the content, not the words.

3. Only when the customer says the salon may choose ("lúc nào vắng thì xếp
   em", "khi nào rảnh cũng được") may you pick the day yourself. If they name
   a service but no day, ask which day first. Never assume today.

4. NEVER act on anyone else's appointments.
   [giữ nguyên văn toàn bộ khối rule 2b cũ, kể cả câu trả lời mẫu]

5. Write exactly ONE reply per turn. Never write the same sentence twice in
   one reply, and never repeat your previous reply verbatim — if they still
   have to choose, write a shorter sentence covering only the choice. Example:
   "Dạ anh chị chọn giờ nào ạ — 8 giờ, 10 giờ hay 10 giờ 15?"

6. Write clock times the way people say them: "3 giờ chiều", "9 giờ rưỡi
   sáng", "1 giờ 45 chiều". Never write "15:00" or "1:45".

VOICE: [giữ nguyên khối cũ, nhưng đổi "address the customer as anh or chị plus
the name in the context block" thành "address the customer exactly as the
'Gọi khách là' line in the context block says"]
```

- [ ] **Step 5: Chạy test cho chắc là pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Đo lại bằng cả 4 kịch bản và so với mốc**

```bash
.venv/bin/python scripts/chat_e2e_transcript.py --scenario all --out after-transcript.txt
grep -c "STREAM: (rỗng" baseline-transcript.txt
grep -c "STREAM: (rỗng" after-transcript.txt
diff -y --width=200 baseline-transcript.txt after-transcript.txt | less
```

Đọc bằng mắt, kiểm đúng bốn điều:

1. Không còn hai lượt nào trả về **cùng một câu**.
2. Câu xác nhận vẫn đủ **ngày + giờ + dịch vụ** và vẫn kết bằng câu hỏi — nhưng khác chữ giữa hai lần đặt.
3. Khách chỉ nói dịch vụ mà chưa nói ngày → bot **hỏi ngày**, không tự chào giờ hôm nay.
4. `parse_time` vẫn được gọi trước `find_free_slots` và `propose_appointment` ở mọi lượt có nhắc thời gian (đọc cột `TOOL`).

Điều 4 là thứ dễ vỡ nhất khi cắt prompt. Nếu nó vỡ: trả rule 5 cũ về prompt và ghi rõ trong báo cáo là docstring không gánh nổi rule đó.

- [ ] **Step 7: Commit**

```bash
git add app/agents/booking_graph/prompts.py app/agents/booking_graph/tools.py tests/test_prompts.py
git commit -m "refactor: chuyển rule của booking về docstring tool, bỏ khuôn câu cứng"
```

---

## Kiểm tra cuối trước khi merge

- [ ] `.venv/bin/python -m pytest -q` xanh (Mongo đang chạy).
- [ ] `grep -rn "REFUSE_MESSAGE\|make_status_tools\|STATUS_PROMPT\|def refuse" app/ tests/` không còn kết quả.
- [ ] `.venv/bin/python scripts/probe_supervisor.py` — lệch 0/14, hoặc phần lệch đã được ghi nhận và giải thích.
- [ ] `after-transcript.txt` đọc bằng mắt, đủ bốn điều ở Task 6 Step 6.
- [ ] Số lượt `STREAM: (rỗng` giảm so với `baseline-transcript.txt`, và những lượt còn lại đều là nhánh đồng-ý của `confirm`.

## Git flow

Theo quy ước repo (việc → `henry/develop` → `main`, không commit thẳng lên `main`). Nhánh `feat/agent-routing-redesign` đã tạo và đã có ba commit spec.

```bash
git checkout henry/develop && git pull --ff-only
git checkout feat/agent-routing-redesign
git rebase henry/develop        # nếu henry/develop đã chạy trước
# ... Task 1..6 ...
git checkout henry/develop
git merge --no-ff feat/agent-routing-redesign
git push origin henry/develop
```
