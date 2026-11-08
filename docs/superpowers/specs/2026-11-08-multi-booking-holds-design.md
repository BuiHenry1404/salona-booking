# Đợt B — Đặt nhiều lịch một lần, giữ chỗ cho các lịch sau

Ngày: 08/11/2026. Tách từ brainstorming "Orchestrator tự nói" theo quyết định chủ dự án (08/11).
**Làm sau** đợt A (`2026-11-08-orchestrator-speaks-design.md`) và chỉ khi đợt A chạy thật đạt.
Spec này giả định đợt A đã có: tool `reply`, `reply_check.py`, nút bấm chạy Orchestrator với quyết
định áp sẵn.

## 1. Vì sao

Khách hay đặt cho cả nhà trong một tin nhắn ("đặt cho chị 3 giờ, với cho con gái 4 giờ"). Hiện
tại mỗi lúc chỉ có **một** đề xuất chờ; các giờ khác vào **hàng chờ** chỉ nhớ giờ, **không giữ
chỗ**, nên tới lượt hỏi thì giờ có thể đã bị người khác đặt. Chủ dự án muốn: xác nhận **từng lịch
một**, và các lịch sau **được giữ chỗ** trong lúc chờ.

## 2. Mục tiêu, không mục tiêu, phản mục tiêu

**Mục tiêu**

1. Khách đặt nhiều lịch trong một tin, xác nhận lần lượt; các lịch sau được giữ chỗ.
2. Khách thêm lịch khi đang chờ xác nhận lịch khác; đổi / huỷ một lịch đang giữ chỗ.
3. Giờ đang giữ bị chặn ở **mọi** đường đặt lịch (chat, web, chủ tiệm đặt hộ).

**Không mục tiêu**: đổi cách ghi lịch (vẫn chỉ `ProposalService.approve`); đổi index
`appointments.slot_keys`.

**Phản mục tiêu**

- Giờ đang giữ cho khách bị người khác đặt mất, kể cả qua web hay chủ tiệm đặt hộ.
- Chỗ giữ đã hết hạn vẫn chặn người khác.
- Khách đồng ý lịch đầu và thêm lịch trong cùng tin → lịch đầu bị bỏ.
- Có đường nào cho LLM ghi lịch.

## 3. Trạng thái đề xuất

- **Đang hỏi** (`pending`): đúng **một** mỗi khách; có nút Đồng ý / Không; `decide_pending` và nút
  bấm **chỉ** nhắm vào đề xuất này.
- **Giữ chỗ chờ hỏi** (`held`): tối đa **2** mỗi khách. Trần tổng 3 *(quyết định người viết spec:
  giữ nhiều giờ một lúc chặn lịch người khác; khách hiếm khi đặt quá 3 lịch một lần)*.
- Đề xuất đang hỏi đóng (duyệt / từ chối / hết hạn / thất bại) → đề xuất `held` **còn hạn** cũ nhất
  thành đang hỏi.
- Mọi chỗ giữ hết hạn **10 phút kể từ khi tạo**, không tính lại khi tới lượt hỏi *(chủ dự án 08/11:
  "giữ lại 10 phút" — hiểu là giữ cách hiện tại; **cần chủ dự án xác nhận** trước khi lập plan)*.
  Hết hạn thì nhả; bối cảnh lượt sau ghi "chỗ giữ vừa hết hạn" để Orchestrator kiểm và đề xuất lại.
- **Thay hẳn hàng chờ cũ** (`QueuedRequest`, `set_queued`, `TimeHint.anchor="queued"` /
  `queue_id`, `queued` của veto, nhánh queue của luật (c)(d)). Lịch nào khách nói rõ giờ thì giữ
  chỗ ngay; lịch chưa rõ giờ thì Orchestrator hỏi cho rõ rồi mới giữ.
- `ProposalService.create` **không** còn tự supersede đề xuất cũ.

## 4. Luồng

### 4.1 Hai lịch trong một tin

1. Orchestrator gọi `appointment_task(propose_book)` cho từng lịch (mỗi vòng một đề xuất): cái đầu
   thành **đang hỏi**, cái sau **giữ chỗ chờ hỏi**. Cả hai giờ bị chặn với người khác.
2. Orchestrator hỏi xác nhận lịch đầu, có thể nhắc sẽ hỏi tiếp lịch sau.
3. Khách đồng ý → lịch đầu ghi; lịch sau thành đang hỏi; Orchestrator hỏi tiếp.
4. Khách từ chối lịch đầu → nhả chỗ; lịch sau vẫn được hỏi tiếp.

### 4.2 Thêm lịch khi đang chờ xác nhận

Khách: "ừ được em, đặt thêm cho cháu chị 5 giờ nữa nha" (đang hỏi lịch 3 giờ).

- **Thứ tự bắt buộc**: Orchestrator **tạo chỗ giữ cho lịch mới trước** (`propose_book` được phép khi
  đề xuất đang hỏi chưa quyết — nới luật (h) đúng chỗ này), **rồi** mới `decide_pending` cho lịch
  đang hỏi.
- `veto()` **bỏ qua giờ của mọi chỗ giữ** (đang có, và vừa tạo trong lượt) khi xét "giờ khác" —
  thay đúng vai trò của tham số `queued` cũ. Nhờ vậy "ừ được" vẫn là `approve`, không bị đổi thành
  `change`.
- Nếu Orchestrator làm ngược thứ tự: veto đổi `approve` thành `change`, lịch đầu bị bỏ — sai nhưng
  không ghi gì sai (mọi lịch vẫn cần khách đồng ý). Prompt nói rõ thứ tự; test ghim thứ tự đúng.
- Sau `approved` trong lượt, `propose_book` (lịch thêm) **được** chạy; `propose_move` /
  `propose_cancel` vẫn bị chặn (luật (j)) *(quyết định người lập: tool đã chặn trùng bằng
  `already_yours`)*.

### 4.3 Đổi / huỷ một lịch đang giữ chỗ chờ hỏi

Khách: "lịch của con gái đổi sang 5 giờ" trong lúc lịch đầu đang được hỏi.

- Tool mới của Orchestrator: **`release_hold(proposal_id)`** — chỉ nhận id của đề xuất `held` của
  khách (enum trong schema, dựng từ bối cảnh); đóng đề xuất (`rejected`) và nhả chỗ. Không ghi lịch,
  không đụng đề xuất đang hỏi. `tests/test_write_fence.py` vẫn chỉ cho `approve` ghi lịch.
- Đổi giờ = `release_hold` rồi `propose_book` giờ mới. Huỷ = `release_hold`.
- Đổi / huỷ chính lịch **đang hỏi** vẫn qua `decide_pending` như hiện tại.

### 4.4 Nút bấm

Nút chỉ hiện cho đề xuất **đang hỏi**. Nút mang id của một đề xuất `held` (hoặc id cũ) → `stale`,
không chạm DB. Sau khi duyệt bằng nút, Orchestrator (đủ tool, quyết định áp sẵn — đợt A) báo kết
quả và hỏi tiếp lịch kế; nếu lịch kế đã hết hạn thì giao Appointment Agent kiểm và đề xuất lại.

## 5. Giữ chỗ chặn mọi đường đặt lịch

- Giờ đang giữ bị chặn ở: `find_free_slots`, tạo đề xuất trong chat, `POST /api/v1/appointments`
  (khách qua web, chủ tiệm đặt hộ với `for_user_id`), dời lịch. Kiểm nằm trong
  `AppointmentService` (create / reschedule) và một service giữ chỗ trong `app/services/`.
- **Nguyên tử**: hai người cùng giữ / đặt một giờ cùng lúc → chỉ một người được. Chỗ giữ hết hạn
  không chặn ai. Chủ tiệm **không** ghi đè; lỗi nói giờ đó đang được giữ tới mấy giờ.
- Index `appointments.slot_keys` partial `{status: "booked"}` giữ nguyên. Cách cài chỗ giữ để người
  lập plan chọn, miễn đủ các điều trên và chạy trên replica set `rs0`.

## 6. User story

**1. Hai lịch trong một tin.**

```
Chị Lan: đặt cho chị 3 giờ chiều mai, với cho con gái chị 4 giờ luôn nha
Bot:     Em giữ chỗ Thứ Hai 9/11, 3 giờ chiều cho chị, chị đồng ý không ạ?
         Xong em hỏi tiếp lịch 4 giờ cho con gái chị nhé.
Chị Lan: [Đồng ý]
Bot:     Dạ em đặt xong lịch 3 giờ chiều cho chị rồi ạ. Còn lịch Thứ Hai 9/11,
         4 giờ chiều cho con gái chị, chị đồng ý không ạ?
```

**2. Thêm lịch khi đang chờ xác nhận.**

```
Bot:     Em giữ chỗ 3 giờ chiều mai cho chị, chị đồng ý không ạ?
Chị Lan: ừ được em, đặt thêm cho cháu chị 5 giờ nữa nha
Bot:     Dạ em đặt xong lịch 3 giờ chiều cho chị rồi ạ. Em giữ chỗ 5 giờ chiều
         mai cho cháu chị, chị đồng ý không ạ?
```

Phía sau: Orchestrator giữ chỗ 17:00 trước, rồi `decide_pending(approve)` cho 15:00; veto bỏ qua
17:00 vì đó là giờ đang giữ.

**3. Đổi lịch đang giữ chỗ.**

```
Chị Lan: lịch của con gái đổi sang 5 giờ nha em
Bot:     Dạ em đổi lịch của con gái chị sang 5 giờ chiều mai rồi, em vẫn giữ chỗ
         cho chị. Lịch 3 giờ chiều của chị, chị đồng ý không ạ?
```

Phía sau: `release_hold(p2)` rồi `propose_book(17:00)`; lịch đang hỏi không đổi.

**4. Chủ tiệm đặt hộ vào giờ đang giữ.**

```
Chủ tiệm đặt 4 giờ chiều mai cho khách gọi điện
Hệ thống: Giờ này đang được giữ cho một khách khác tới 10:25. Vui lòng chọn
          giờ khác hoặc thử lại sau.
```

## 7. Thế nào là xong

- `pytest` toàn bộ xanh; có test: hai khách cùng giữ một giờ → một người được; REST / chủ tiệm
  vào giờ đang giữ của khách khác → 409; chỗ giữ hết hạn thì đặt được; thứ tự "giữ trước, quyết
  sau" giữ được lịch đầu.
- Kịch bản chạy thật thêm các lượt: hai lịch một tin, thêm lịch khi đang chờ, đổi lịch đang giữ —
  chạy **2 lần** (hỏi chủ dự án trước), cả hai đạt như tiêu chí đợt A và: chỗ giữ của lịch sau còn
  hiệu lực trong lúc hỏi lịch đầu; DB cuối đúng.

## Các quyết định người viết spec tự đưa ra

1. Trần 1 đang hỏi + 2 giữ chỗ chờ hỏi.
2. Giữ chỗ thay hẳn hàng chờ cũ.
3. Tool `release_hold` cho đổi / huỷ lịch đang giữ chỗ.
4. Thứ tự "giữ chỗ lịch mới trước, quyết lịch đang hỏi sau"; veto bỏ qua giờ đang giữ.
5. Nới luật (j) cho `propose_book` sau `approved`.
6. Chủ tiệm không ghi đè chỗ đang giữ.
