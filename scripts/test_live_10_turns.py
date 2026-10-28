"""Script clear DB và chạy 10 lượt hội thoại LLM thật để kiểm tra toàn bộ luồng
với các edge-case, log chi tiết từng step, từng agent và từng tool được gọi.
"""
import asyncio
import json
import sys
from datetime import datetime
from typing import Any, Dict, List

from motor.motor_asyncio import AsyncIOMotorClient

from app.agents.booking_graph.events import run_turn
from app.core.clock import TZ, now_utc, to_local
from app.core.config import settings
from app.infrastructure.database import ensure_indexes
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.appointment import AppointmentService
from app.services.conversation import ConversationService
from app.services.proposal import ProposalService
from app.services.auth import AuthService
from scripts.seed_dev_users import DEV_USERS


async def clear_database(db):
    """Xoá sạch các collection hội thoại và lịch hẹn, giữ cấu trúc sạch."""
    collections_to_clear = [
        "appointments",
        "checkpoint_writes",
        "checkpoints",
        "conversation_sessions",
        "conversations",
        "message_inbox",
        "proposals",
        "rate_limits",
        "refresh_tokens",
        "users",
    ]
    print("\n" + "=" * 60)
    print("🧹 BƯỚC 1: CLEAR DATABASE")
    print("=" * 60)
    for col in collections_to_clear:
        count = await db[col].count_documents({})
        await db[col].delete_many({})
        print(f"  - Đã xoá {count:4d} documents trong '{col}'")

    # Đảm bảo shop_status rảnh và shop_hours chuẩn
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
    print("  - Đặt lại shop_status: is_busy=False, shop_hours: 08:00 - 19:00")

    # Đảm bảo index
    await ensure_indexes(db)
    print("  - Đảm bảo indexes thành công.")

    # Seed lại users chuẩn
    auth_svc = AuthService(db)
    for phone, password, full_name, role in DEV_USERS:
        await auth_svc.create_user(phone, password, full_name, role=role)
        print(f"  - Đã tạo user: {phone} ({full_name})")


async def execute_turn(turn_idx: int, db, user: User, user_text: str, description: str):
    print("\n" + "#" * 70)
    print(f"📍 LƯỢT {turn_idx}: {description}")
    print(f"👤 Khách nói: \"{user_text}\"")
    print("-" * 70)

    tools_called: List[Dict[str, Any]] = []
    accumulated_tokens: List[str] = []
    final_complete: Dict[str, Any] = {}
    error_event = None

    async for event in run_turn(db, user, user_text):
        evt_type = event.type
        evt_data = event.data

        if evt_type == "tool_started":
            print(f"  🛠️  [TOOL START]  name={evt_data.get('name')}")
            tools_called.append({"name": evt_data.get("name")})
        elif evt_type == "tool_finished":
            print(f"  ✅ [TOOL FINISH] name={evt_data.get('name')} | ok={evt_data.get('ok')}")
            if tools_called and tools_called[-1]["name"] == evt_data.get("name"):
                tools_called[-1]["ok"] = evt_data.get("ok")
        elif evt_type == "token":
            accumulated_tokens.append(evt_data.get("text", ""))
        elif evt_type == "complete":
            final_complete = evt_data
        elif evt_type == "error":
            error_event = evt_data
            print(f"  ❌ [ERROR] {evt_data}")

    # Đọc lại trạng thái Graph, Working Memory và DB
    from app.agents.booking_graph.graph import build_graph
    from app.agents.booking_graph.events import pick_thread
    from app.core.clock import now_ms

    graph = build_graph(db, user)
    thread_id = await pick_thread(graph, str(user.id), now_ms())
    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    values = snap.values or {}
    route = values.get("route")
    gate_next = values.get("gate_next")
    answer_source = values.get("answer_source")

    # Thu thập chi tiết tool calls từ messages
    recent_tool_details = []
    for msg in values.get("messages", [])[-6:]:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                recent_tool_details.append({"name": tc.get("name"), "args": tc.get("args")})

    conv_state = await ConversationService(db).get_state(str(user.id))
    slots = conv_state.slots if conv_state else None
    tool_day = conv_state.tool_day if conv_state else None
    latest_proposal = await ProposalService(db).latest(user)
    appts = await AppointmentService(db).upcoming_for(user)

    print("\n  📊 [TRẠNG THÁI CUỘC TRÒ CHUYỆN SAU LƯỢT]")
    print(f"     - Agent route    : {route} (gate_next={gate_next}, source={answer_source})")
    if recent_tool_details:
        print(f"     - Tool calls gần : {recent_tool_details}")
    if slots:
        print(f"     - slots.intent   : {slots.intent}")
        print(f"     - slots.day      : {slots.day}")
        print(f"     - slots.time     : {slots.time}")
        print(f"     - slots.service  : {slots.service}")
        print(f"     - slots.declined : {slots.declined}")
        print(f"     - slots.stated   : {slots.stated}")
    else:
        print("     - slots          : None")
    print(f"     - state.tool_day : {tool_day}")

    if latest_proposal:
        local_prop = to_local(latest_proposal.start_at)
        print(f"  📋 [ĐỀ XUẤT GẦN NHẤT] id={latest_proposal.id} | status={latest_proposal.status} | start_at={local_prop.isoformat()}")

    if appts:
        print(f"  📅 [LỊCH HẸN TRÊN DB] có {len(appts)} lịch:")
        for a in appts:
            print(f"     * Appt #{a.id}: {to_local(a.start_at).isoformat()} ({a.note}) - status={a.status}")
    else:
        print("  📅 [LỊCH HẸN TRÊN DB] Chưa có lịch nào được duyệt.")

    answer = final_complete.get("answer", "")
    prop_payload = final_complete.get("proposal")
    print(f"\n  🤖 Bot đáp: \"{answer}\"")
    if prop_payload:
        print(f"     (Kèm nút xác nhận: id={prop_payload.get('id')}, when={prop_payload.get('when_spoken')})")
    
    return {
        "turn": turn_idx,
        "input": user_text,
        "route": route,
        "tools": recent_tool_details if recent_tool_details else tools_called,
        "slots": slots.model_dump() if slots else None,
        "tool_day": tool_day,
        "answer": answer,
        "proposal": prop_payload,
        "error": error_event,
    }


async def main():
    client = AsyncIOMotorClient(str(settings.mongo_uri))
    db = client[settings.mongo_db_name]

    # 1. Clear database
    await clear_database(db)

    # 2. Lấy thông tin user thử nghiệm: Cô Lan
    user_repo = UserRepository(db)
    user = await user_repo.get_by_phone("0912345678")
    if not user:
        print("❌ Không tìm thấy user 0912345678!")
        client.close()
        return

    print(f"\n👤 BẮT ĐẦU TEST VỚI KHÁCH: {user.full_name} (phone: {user.phone}, id: {user.id})")

    # Kịch bản 10 lượt kiểm tra các edge case
    scenario = [
        (
            1,
            "chào em, mai chị muốn ghé làm móng",
            "Edge Case 1: Khách nói ngày tương đối ('mai') nhưng chưa nói giờ cụ thể -> hệ thống phải hỏi giờ, lưu ngày mai vào Working Memory",
        ),
        (
            2,
            "tầm 3 giờ được không em",
            "Edge Case 2: Khách nói giờ không rõ buổi ('3 giờ') -> auto-anchor ngày mai từ Working Memory, hỏi lại sáng hay chiều",
        ),
        (
            3,
            "3 giờ chiều nha em",
            "Edge Case 3: Khách chốt buổi chiều -> giải ra 15:00 ngày mai, tạo đề xuất (proposal) và interrupt chờ xác nhận",
        ),
        (
            4,
            "dạ thôi 3 giờ chiều đó bận rồi, đổi sang 4 giờ chiều mai đi em",
            "Edge Case 4: Khách từ chối và đổi giờ cùng lượt -> confirm decline 15:00, chuyển context cùng lượt cho booking agent tạo đề xuất 16:00 ngày mai",
        ),
        (
            5,
            "tiệm mình ở địa chỉ nào với mở cửa đến mấy giờ em?",
            "Edge Case 5: Khách hỏi chuyện phụ sang Shop Agent -> Shop Agent trả lời giờ mở cửa/địa chỉ, không làm mất đề xuất và Working Memory",
        ),
        (
            6,
            "vậy chốt lịch 4 giờ chiều cho chị nha",
            "Edge Case 6: Khách quay lại chốt 4h chiều mà không nhắc lại ngày -> hệ thống nhận diện đúng đề xuất đang chờ",
        ),
        (
            7,
            "ừ đặt cho chị đi em",
            "Edge Case 7: Khách đồng ý chốt lịch -> Confirm node duyệt, Appointment được ghi vào DB, xóa active target trong Working Memory",
        ),
        (
            8,
            "cảm ơn em nhiều nha",
            "Edge Case 8: Khách chào cảm ơn -> Social Agent trả lời lịch sự, không gọi tool đặt lịch",
        ),
        (
            9,
            "thứ Tư tuần sau lúc 9 giờ sáng chị ghé gội đầu dưỡng sinh được không?",
            "Edge Case 9: Khách đặt lịch mới vào tuần sau -> parse đúng ngày tuần sau (nhờ 14-day calendar), TUYỆT ĐỐI KHÔNG neo vào lịch đã approved hôm trước",
        ),
        (
            10,
            "chị xem lại lịch hẹn của chị với",
            "Edge Case 10: Khách tra cứu lại danh sách lịch hẹn -> hệ thống liệt kê đúng lịch đã đặt thành công",
        ),
    ]

    results = []
    for turn_idx, text, desc in scenario:
        res = await execute_turn(turn_idx, db, user, text, desc)
        results.append(res)
        await asyncio.sleep(1)  # Giãn nhẹ giữa các lượt

    client.close()

    print("\n" + "=" * 70)
    print("🏁 TỔNG KẾT 10 LƯỢT TEST THỰC TẾ:")
    print("=" * 70)
    bugs_found = []
    for r in results:
        status_icon = "❌" if r["error"] else "✅"
        print(f"  {status_icon} Lượt {r['turn']}: {r['input']}")
        print(f"     Tools: {[t['name'] for t in r['tools']]}")
        print(f"     Bot: {r['answer'][:80]}...")
        if r["error"]:
            bugs_found.append(f"Lượt {r['turn']}: {r['error']}")

    if bugs_found:
        print(f"\n⚠️ PHÁT HIỆN {len(bugs_found)} VẤN ĐỀ:")
        for b in bugs_found:
            print(f"  - {b}")
    else:
        print("\n🎉 TOÀN BỘ 10 LƯỢT ĐỀU HOÀN THÀNH HOÀN HẢO KHÔNG CÓ LỖI (0 ERRORS)!")


if __name__ == "__main__":
    asyncio.run(main())
