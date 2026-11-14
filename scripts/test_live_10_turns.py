"""Kịch bản chạy thật 10 lượt cho gói `app/agents/salon/` (Task 18 — chạy
thử thuộc Task 19, chủ dự án duyệt từng lần). Gọi Azure OpenAI thật, KHÔNG chạy
tự động — chỉ `py_compile` trong CI/kiểm thử (xem `.superpowers/sdd/...`).

    docker compose up -d mongo
    PYTHONPATH=. .venv/bin/python scripts/test_live_10_turns.py             # xoá DB, kịch bản A
    PYTHONPATH=. .venv/bin/python scripts/test_live_10_turns.py --keep-db   # giữ DB, kịch bản B

Kịch bản B (`--keep-db`, chủ dự án 11/11): KHÔNG xoá gì, tạo một khách mới
(Chị Mai, số điện thoại chưa có) và chạy 10 lượt trên lịch ĐANG CÓ của DB dev.
Trước khi gọi Azure, script tự đọc hai lịch đã đặt sớm nhất của KHÁCH KHÁC vào
ngày mai (`pick_taken_starts`) để hỏi đúng giờ đang bận, và dừng — không gọi
Azure — nếu DB chưa có đủ hai lịch đó. Hai lượt "chốt giờ" (4 và 8) đọc lựa
chọn đầu tiên của phiên chờ Đặt lịch đang mở (`pick_session_option`) và dừng
nếu phiên không còn lựa chọn nào.

Trình tự: xoá sạch dữ liệu hội thoại/lịch, seed lại user dev + danh mục dịch
vụ (`scripts/seed_services.py`), tạo sẵn MỘT lịch chặn lúc 15:00 ngày mai
(khách khác, tiệm phục vụ từng khách một — CONTEXT.md quyết định 12) rồi chạy
10 lượt chat của Cô Lan qua `app.agents.salon.run_turn`:

 1. Chào hỏi.
 2. Đặt sơn gel 3 giờ chiều mai — giờ đó đã có người (lịch chặn ở trên).
 3. Hỏi giờ mở cửa (lạc đề, không được làm mất phiên đang chờ của Đặt lịch).
 4. "4 giờ đi" — tiếp phiên đang chờ bằng giờ mới.
 5. Bấm Đồng ý trên đề xuất (nút bấm = API bằng code, không gọi model).
 6. Hỏi giá nhuộm tóc.
 7. Đặt nhuộm tóc sáng mai.
 8. Đồng ý bằng chữ (gõ chữ, qua Decision Gate, không bấm nút).
 9. Cảm ơn.
10. Xem lại lịch hẹn.

Mỗi lượt in: câu khách, các tool đã gọi, câu bot đáp, nguồn câu (llm/code,
đọc từ `salon_turns` — ledger của gói mới), thời gian chạy lượt, và trạng thái
nghiệp vụ trong Mongo sau lượt (phiên chờ, đề xuất gần nhất, lịch sắp tới).
"""
import argparse
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient

from app.agents.salon import ButtonReply, run_turn
from app.agents.salon.turn.ledger import LEDGER_COLLECTION
from app.core.clock import TZ, local_day_bounds, now_utc, to_local
from app.core.config import settings
from app.core.vi_format import format_vi_hhmm
from app.infrastructure.database import ensure_indexes
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.appointment import AppointmentService
from app.services.auth import AuthService
from app.services.proposal import ProposalService
from app.services.waiting_session import WaitingSessionService
from scripts.seed_dev_users import DEV_USERS
from scripts.seed_services import DATA_PATH
from app.models.service import Service
from app.services.service_catalog import ServiceCatalogService
import json


async def clear_database(db) -> None:
    """Xoá sạch collection hội thoại/lịch, giữ cấu trúc sạch cho một lần chạy
    độc lập (không phải `drop_legacy_agent_data.py` — đó là việc dọn một lần
    của dữ liệu gói cũ, đã chạy trên DB dev 10/11)."""
    collections_to_clear = [
        "appointments",
        "checkpoint_writes",
        "checkpoints",
        "conversation_threads",
        "conversations",
        "message_inbox",
        "proposals",
        "rate_limits",
        "refresh_tokens",
        "salon_memories",
        LEDGER_COLLECTION,
        "services",
        "users",
        "waiting_sessions",
    ]
    print("\n" + "=" * 60)
    print("BƯỚC 1: XOÁ SẠCH DỮ LIỆU CŨ")
    print("=" * 60)
    for col in collections_to_clear:
        count = await db[col].count_documents({})
        await db[col].delete_many({})
        print(f"  - đã xoá {count:4d} tài liệu trong '{col}'")

    await db["shop_status"].update_one(
        {"_id": "singleton"},
        {"$set": {"is_busy": False, "busy_until": None, "updated_at": now_utc()}},
        upsert=True,
    )
    await db["shop_hours"].update_one(
        {"_id": "singleton"},
        {"$set": {"open_time": "08:00", "close_time": "19:00", "closed_days": []}},
        upsert=True,
    )
    print("  - đặt lại shop_status: rảnh, shop_hours: 08:00 - 19:00")

    await ensure_indexes(db)
    print("  - đã đảm bảo index")

    auth_svc = AuthService(db)
    for phone, password, full_name, role in DEV_USERS:
        await auth_svc.create_user(phone, password, full_name, role=role)
    print(f"  - đã tạo {len(DEV_USERS)} tài khoản dev")


async def seed_services(db) -> None:
    """Nạp danh mục dịch vụ mock (như `scripts/seed_services.py`, gọi lại tại
    đây để script này tự đứng độc lập trong cùng một kết nối Mongo)."""
    print("\n" + "=" * 60)
    print("BƯỚC 2: NẠP DANH MỤC DỊCH VỤ")
    print("=" * 60)
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    services = [Service(**row) for row in raw]
    count = await ServiceCatalogService(db).upsert_many(services)
    print(f"  - đã nạp {count} dịch vụ")


async def create_blocking_appointment(db) -> datetime:
    """Tạo sẵn lịch của MỘT khách khác (Chú Hùng) lúc 15:00 ngày mai — tiệm
    phục vụ từng khách một nên giờ này coi như kín, bất kể dịch vụ. Không gắn
    dịch vụ → dài `booking_slot_minutes` (60'), chặn 15:00–16:00: lượt 4 "4 giờ
    đi" phải đặt được (live run 1 dùng úp móng 90' nên 16:00 cũng kín). Trả về
    mốc giờ VN đã chặn để in ra màn hình."""
    print("\n" + "=" * 60)
    print("BƯỚC 3: TẠO LỊCH CHẶN 15:00 NGÀY MAI (khách khác)")
    print("=" * 60)
    chu_hung = await UserRepository(db).get_by_phone("0923456789")
    tomorrow_local = to_local(now_utc()).date() + timedelta(days=1)
    blocked_at = datetime.combine(tomorrow_local, datetime.min.time(), tzinfo=TZ).replace(hour=15)
    appt = await AppointmentService(db).create(
        chu_hung, blocked_at, "Lịch chặn test (Task 18)",
    )
    print(f"  - đã đặt cho {chu_hung.full_name}: {to_local(appt.start_at).isoformat()} "
          f"({appt.duration_minutes} phút)")
    return blocked_at


async def dump_state(db, user: User) -> Dict[str, Any]:
    """Trạng thái nghiệp vụ đọc lại từ Mongo — gói salon không giữ phiên /
    đề xuất / lịch trong state của graph (docs/architech-agent.md luật 7, 8)."""
    sessions = await WaitingSessionService(db).active(str(user.id), now=now_utc())
    latest_proposal = await ProposalService(db).latest(user)
    appointments = await AppointmentService(db).upcoming_for(user)
    return {"sessions": sessions, "proposal": latest_proposal, "appointments": appointments}


def print_state(state: Dict[str, Any]) -> None:
    sessions = state["sessions"]
    if sessions:
        for agent, session in sessions.items():
            print(f"     - phiên chờ [{agent}]: việc={session.task!r} "
                  f"thiếu trường={session.question.fields!r} "
                  f"hết hạn={to_local(session.expires_at).isoformat()}")
    else:
        print("     - phiên chờ      : không có")

    proposal = state["proposal"]
    if proposal is not None:
        print(f"     - đề xuất gần nhất: id={proposal.id} kind={proposal.kind} "
              f"status={proposal.status} start_at={to_local(proposal.start_at).isoformat()}")
    else:
        print("     - đề xuất gần nhất: không có")

    appointments = state["appointments"]
    if appointments:
        print(f"     - lịch sắp tới ({len(appointments)}):")
        for appt in appointments:
            print(f"        * #{appt.id} {to_local(appt.start_at).isoformat()} "
                  f"({appt.duration_minutes} phút) status={appt.status}")
    else:
        print("     - lịch sắp tới   : chưa có")


async def answer_source_of_latest_turn(db, user: User) -> Optional[str]:
    """Nguồn của câu đáp lượt vừa chạy — đọc `salon_turns` (ledger mới,
    `TurnLedger`), không suy đoán từ sự kiện Socket.IO."""
    doc = await db[LEDGER_COLLECTION].find_one(
        {"user_id": str(user.id)}, sort=[("asked_at", -1)]
    )
    return doc.get("answer_source") if doc else None


async def execute_turn(turn_idx: int, db, user: User, description: str,
                       question: "str | ButtonReply") -> Dict[str, Any]:
    label = question.decision if isinstance(question, ButtonReply) else question
    print("\n" + "#" * 70)
    print(f"LƯỢT {turn_idx}: {description}")
    print(f"Khách: {label!r}")
    print("-" * 70)

    tools_called: List[Dict[str, Any]] = []
    final_complete: Dict[str, Any] = {}
    error_event: Optional[Dict[str, Any]] = None

    started = time.monotonic()
    async for event in run_turn(db, user, question):
        if event.type == "tool_started":
            print(f"  [TOOL BẮT ĐẦU] {event.data.get('name')} — {event.data.get('label')}")
            tools_called.append({"name": event.data.get("name")})
        elif event.type == "tool_finished":
            print(f"  [TOOL XONG]    {event.data.get('name')} ok={event.data.get('ok')}")
        elif event.type == "complete":
            final_complete = event.data
        elif event.type == "error":
            error_event = event.data
            print(f"  [LỖI] {event.data}")
    elapsed = time.monotonic() - started

    source = await answer_source_of_latest_turn(db, user)
    state = await dump_state(db, user)

    answer = final_complete.get("answer", "")
    proposal_payload = final_complete.get("proposal")
    print(f"\n  Bot đáp ({elapsed:.2f}s, nguồn={source}): {answer!r}")
    if proposal_payload:
        print(f"     kèm nút xác nhận: id={proposal_payload.get('id')} "
              f"khi={proposal_payload.get('when_spoken')}")
    print("  Trạng thái sau lượt:")
    print_state(state)

    return {
        "turn": turn_idx, "tools": tools_called, "answer": answer, "source": source,
        "elapsed": elapsed, "proposal": proposal_payload, "error": error_event,
    }


# ---------------------------------------------------------------------------
# Kịch bản B — giữ DB, khách mới
# ---------------------------------------------------------------------------

PICK_OPTION = "<pick an option of the open booking session>"  # sentinel for turns 4, 8


def _as_utc(dt: datetime) -> datetime:
    """Mongo trả `datetime` naive khi client không mở `tz_aware=True` — coi
    naive là UTC (Mongo luôn lưu UTC), không để `to_local`/`astimezone` đoán
    nhầm múi giờ máy chủ (bước Finding 2)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def customer_clock_phrase(start: datetime) -> str:
    """Câu giờ khách sẽ gõ, suy từ giờ VN của `start` (ví dụ "4 giờ chiều")."""
    local = to_local(start)
    return format_vi_hhmm(f"{local.hour:02d}:{local.minute:02d}")


async def pick_taken_starts(db, *, exclude_user_id: str) -> Optional[tuple[datetime, datetime]]:
    """Hai mốc giờ sớm nhất, khác nhau, của lịch ĐÃ ĐẶT ngày mai của KHÁCH
    KHÁC (loại `exclude_user_id` — khách mới của kịch bản B chưa có lịch nào,
    nhưng loại cho chắc). `None` nếu chưa đủ hai mốc — script dừng trước khi
    gọi Azure thay vì chạy lệch dữ liệu (bài học live run 1)."""
    tomorrow_local = to_local(now_utc()).date() + timedelta(days=1)
    start_utc, end_utc = local_day_bounds(tomorrow_local)
    cursor = db["appointments"].find(
        {
            "status": "booked",
            "user_id": {"$ne": exclude_user_id},
            "start_at": {"$gte": start_utc, "$lt": end_utc},
        },
        sort=[("start_at", 1)],
    )
    distinct: List[datetime] = []
    async for doc in cursor:
        start = _as_utc(doc["start_at"])
        if start not in distinct:
            distinct.append(start)
        if len(distinct) == 2:
            break
    if len(distinct) < 2:
        return None
    return distinct[0], distinct[1]


async def pick_session_option(db, *, user_id: str) -> Optional[datetime]:
    """Lựa chọn đầu tiên của phiên chờ Đặt lịch đang mở của khách; `None` nếu
    không có phiên hoặc phiên không còn lựa chọn nào."""
    sessions = await WaitingSessionService(db).active(user_id, now=now_utc())
    session = sessions.get("appointment")
    if session is None or not session.question.options:
        return None
    return _as_utc(datetime.fromisoformat(session.question.options[0].iso))


def keep_db_turns(first_taken: datetime, second_taken: datetime) -> list[tuple]:
    """10 lượt kịch bản B — giờ bận (lượt 2, 7) suy từ `first_taken` /
    `second_taken` (hai lịch đã đặt sớm nhất của khách khác, đọc từ DB),
    lượt 4 và 8 (`PICK_OPTION`) đọc lựa chọn của phiên chờ lúc chạy."""
    return [
        (1, "Chào hỏi, khách mới.", "Chào em, chị là khách mới, lần đầu nhắn tiệm"),
        (2, f"Đặt sơn gel lúc {customer_clock_phrase(first_taken)} mai — giờ này đang bận.",
         f"Em ơi mai chị muốn sơn gel lúc {customer_clock_phrase(first_taken)}"),
        (3, "Hỏi giá sơn gel giữa chừng (phiên Đặt lịch vẫn phải còn chờ).",
         "Mà sơn gel bên em bao nhiêu tiền vậy em?"),
        (4, "Chốt giờ trống cho phiên đang chờ.", PICK_OPTION),
        (5, "Bấm Đồng ý trên đề xuất (nút bấm, không gọi model).", None),
        (6, "Hỏi tiệm có mở Chủ nhật không.", "Chủ nhật tiệm có mở cửa không em?"),
        (7, f"Đặt thêm cắt tóc lúc {customer_clock_phrase(second_taken)} mai — giờ này đang bận.",
         f"Chị muốn đặt thêm cắt tóc lúc {customer_clock_phrase(second_taken)} mai nữa"),
        (8, "Chốt giờ trống cho phiên đang chờ.", PICK_OPTION),
        (9, "Đồng ý bằng chữ (không bấm nút, qua Decision Gate).", "Ừ chị đồng ý"),
        (10, "Xem lại lịch hẹn.", "Chị xem lại lịch hẹn của chị với"),
    ]


async def create_new_customer(db) -> User:
    """Khách mới cho mỗi lần chạy kịch bản B — số điện thoại chưa có trong DB."""
    users = UserRepository(db)
    seed = int(time.time())
    while True:
        phone = f"08{seed % 10**8:08d}"
        if await users.get_by_phone(phone) is None:
            break
        seed += 1
    user = await AuthService(db).create_user(phone, "test123456", "Chị Mai", role="user")
    print(f"  - đã tạo khách mới: {user.full_name} ({user.phone})")
    return user


async def main(keep_db: bool = False) -> None:
    client = AsyncIOMotorClient(str(settings.mongo_uri), tz_aware=True)
    db = client[settings.mongo_db_name]

    if keep_db:
        # Finding 3: pick the taken times BEFORE creating the new customer —
        # a customer that does not exist yet cannot own any appointment, so
        # `exclude_user_id=""` excludes nothing and is safe. If the DB turns
        # out not to have two taken times, we stop here without creating an
        # orphan user.
        starts = await pick_taken_starts(db, exclude_user_id="")
        if starts is None:
            print("\nDừng: DB chưa có đủ hai lịch đã đặt của khách khác vào "
                  "ngày mai, không gọi Azure.")
            client.close()
            return
        first_taken, second_taken = starts
        user = await create_new_customer(db)
        turns = keep_db_turns(first_taken, second_taken)
    else:
        await clear_database(db)
        await seed_services(db)
        blocked_at = await create_blocking_appointment(db)
        user = await UserRepository(db).get_by_phone("0912345678")
        if user is None:
            print("Không tìm thấy user 0912345678!")
            client.close()
            return
        turns = scenario_a_turns(blocked_at)
    print(f"\nBắt đầu {len(turns)} lượt với khách: {user.full_name} ({user.phone})")

    results: List[Dict[str, Any]] = []
    proposal_id: Optional[str] = None

    for turn_idx, description, text in turns:
        if text is None:
            if proposal_id is None:
                print(f"\n  [BỎ QUA] lượt {turn_idx}: chưa có đề xuất đang chờ để bấm Đồng ý.")
                continue
            question: "str | ButtonReply" = ButtonReply(proposal_id=proposal_id, decision="yes")
        elif text == PICK_OPTION:
            option = await pick_session_option(db, user_id=str(user.id))
            if option is None:
                print(f"\n  [DỪNG] lượt {turn_idx}: phiên chờ không có lựa chọn.")
                break
            question = f"{customer_clock_phrase(option)} đi em"
        else:
            question = text
        result = await execute_turn(turn_idx, db, user, description, question)
        results.append(result)
        if result["proposal"]:
            proposal_id = result["proposal"]["id"]
        await asyncio.sleep(1)  # giãn nhẹ giữa các lượt (tránh dồn trace Langfuse)

    client.close()

    print("\n" + "=" * 70)
    print(f"TỔNG KẾT {len(turns)} LƯỢT")
    print("=" * 70)
    for r in results:
        icon = "LỖI" if r["error"] else "OK "
        print(f"  [{icon}] lượt {r['turn']:>2} | nguồn={r['source']} | "
              f"{r['elapsed']:.2f}s | {r['answer'][:70]!r}")
    errors = [r for r in results if r["error"]]
    if errors:
        print(f"\n{len(errors)} lượt lỗi:")
        for r in errors:
            print(f"  - lượt {r['turn']}: {r['error']}")
    else:
        print("\nKhông lượt nào trả về error.")


def scenario_a_turns(blocked_at: datetime) -> List[tuple]:
    """Kịch bản A (xoá DB): 10 lượt của Cô Lan. `None` = bấm nút Đồng ý."""
    return [
        (1, "Chào hỏi mở đầu.", "Chào em, chị mới qua tiệm lần đầu nè"),
        (2, f"Đặt sơn gel 3 giờ chiều mai — giờ này đã có khách khác "
            f"({to_local(blocked_at).isoformat()}).",
         "Em ơi chị muốn đặt sơn gel lúc 3 giờ chiều mai cho chị"),
        (3, "Hỏi giờ mở cửa (lạc đề, phiên Đặt lịch vẫn phải còn chờ).",
         "À mà tiệm mình mở cửa tới mấy giờ vậy em?"),
        (4, "Chốt giờ mới cho phiên đang chờ.", "4 giờ đi"),
        (5, "Bấm Đồng ý trên đề xuất (nút bấm, không gọi model).", None),
        (6, "Hỏi giá dịch vụ nhuộm tóc.", "Nhuộm tóc bên em giá nhiêu vậy em?"),
        (7, "Đặt nhuộm tóc sáng mai.", "Chị muốn đặt nhuộm tóc sáng mai lúc 9 giờ cho chị"),
        (8, "Đồng ý bằng chữ (không bấm nút, qua Decision Gate).", "Ừ chị đồng ý"),
        (9, "Cảm ơn.", "Cảm ơn em nhiều nha"),
        (10, "Xem lại lịch hẹn.", "Chị xem lại lịch hẹn của chị với"),
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep-db", action="store_true",
                        help="giữ DB dev, tạo khách mới, chạy kịch bản B")
    asyncio.run(main(keep_db=parser.parse_args().keep_db))
