"""Tầng trạng thái hội thoại trong phiên: nén phần cũ của hội thoại hôm nay thành dữ kiện.

Spec: docs/superpowers/specs/2026-09-19-conversation-state-design.md.
"""
import asyncio
import re
from datetime import date, datetime, time as dtime, timedelta
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agents.llm import build_chat_model
from app.core.clock import ensure_aware, now_utc, to_local
from app.core.logging import get_logger
from app.core.text import single_line
from app.models.appointment import NOTE_MAX
from app.models.conversation import AskedItem, ChatMessage, ConversationSlots, StateDigest
from app.services.conversation import CHARS_PER_TOKEN, ConversationService

if TYPE_CHECKING:  # tránh vòng import: analyzer.py cần clean_free_text_item ở đây.
    from app.agents.booking_graph.analyzer import TurnAnalysis

logger = get_logger(__name__)

KEEP_RECENT_TURNS = 4                 # 8 tin gần nhất luôn nguyên văn
COMPACT_THRESHOLD_TOKENS = 800        # phần ngoài cửa sổ vượt mức này mới nén
MAX_BULLETS = 8                       # trần cũ, giữ cho tài liệu `summary` cũ
BULLET_MAX_CHARS = 120                # trần ký tự của MỖI mục digest + mỗi câu gốc khách
MAX_FAILURES = 3                      # hỏng liên tiếp ngần này thì thôi tới hết ngày
STATE_TIMEOUT_SECONDS = 8            # cùng mốc với parser (CONTEXT.md bẫy #10)

# Trần số mục mỗi phần của StateDigest (Task 12c, spec mục 3) — khớp tên field
# để sanitize_digest/merge_digest/DIGEST_PROMPT dùng chung MỘT nguồn.
DIGEST_CAPS = {"wants": 4, "settled": 4, "declined": 4, "constraints": 4, "open": 3}
MAX_CUSTOMER_QUOTES = 3               # câu gốc khách, do code chọn (không LLM)


class StateOutput(BaseModel):
    """Đầu ra có cấu trúc của lượt nén: digest 5 mục + dữ kiện dạng trường.

    `digest`/`slots` cố ý KHÔNG validate nghiêm ở đây: lớp gọi fail-soft, ném
    lỗi là vứt luôn phần model đã nén đúng. `sanitize_digest`/`sanitize_slots`
    lọc sau khi parse — `sanitize_slots` cần `now` cùng danh sách id thật.
    """

    digest: StateDigest = Field(default_factory=StateDigest)
    slots: Optional[ConversationSlots] = None


def estimate_tokens(messages: Sequence[ChatMessage]) -> int:
    return sum(max(1, len(m.content) // CHARS_PER_TOKEN) for m in messages)


def split_window(messages, covers_until: Optional[datetime]):
    """(older, recent): recent = 2*KEEP_RECENT_TURNS tin cuối, older = phần
    trước đó mà state chưa phủ. Nhận cả list số trong test — hàm chỉ cắt lát
    và lọc theo created_at khi có."""
    keep = 2 * KEEP_RECENT_TURNS
    recent = list(messages[-keep:]) if keep else []
    older = list(messages[:-keep]) if len(messages) > keep else []
    if covers_until is not None:
        older = [m for m in older if m.created_at > covers_until]
    return older, recent


class ConversationStateService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.conversations = ConversationService(db)

    async def _todays_messages(self, user_id: str) -> List[ChatMessage]:
        """Cùng mốc cắt với ConversationService.history() — chủ sở hữu công
        thức là `ConversationService._session_cutoff()`, không lặp lại ở đây."""
        return await self.conversations.session_messages(user_id)

    async def _set_today_fields(self, user_id: str, fields: dict, now: datetime) -> None:
        """`$set` ĐÚNG các trường `state.*` trong `fields` của state HÔM NAY —
        không bao giờ ghi CẢ `state` (review Task 12/12b). Lượt nén
        (`_compact`) và bộ phân tích (`apply_turn_analysis`) mỗi bên chỉ sở
        hữu một phần của `state`; ghi cả document là xoá phần của bên kia (vd.
        nén xoá `stated`/`asked`) và giẫm lên lần ghi song song.

        MỘT lệnh update dạng pipeline (nguyên tử trên document), xử lý luôn
        ba ca mà `$set` đường chấm thường bị vấp:
        - chưa có document → upsert (`messages: []`, `created_at`);
        - `state` thiếu hoặc của NGÀY KHÁC → thay bằng state rỗng của hôm nay
          (`covers_until` = epoch — `now` sẽ giấu mọi tin hôm nay khỏi
          `context_window`, review Task 12 #1), không để sót dữ kiện hôm qua;
        - `state.slots` là null (`set_state`/`bump_state_failures` ghi
          `slots: None`) → `{}`: `$set state.slots.x` trên null là lỗi Mongo.
        Giá trị bọc `$literal`: chữ khách (`stated`) bắt đầu bằng "$" không
        được hiểu thành đường dẫn trường trong pipeline.
        """
        today = to_local(now).date()
        day = datetime(today.year, today.month, today.day)
        fresh = {"day": day, "covers_until": datetime(1970, 1, 1, tzinfo=now.tzinfo),
                 "summary": [], "digest": None, "customer_quotes": [],
                 "updated_at": now, "failures": 0, "slots": {}}
        await self.conversations.collection.update_one(
            {"user_id": user_id},
            [
                {"$set": {
                    "state": {"$cond": [{"$eq": ["$state.day", day]}, "$state",
                                        {"$literal": fresh}]},
                    "messages": {"$ifNull": ["$messages", []]},
                    "created_at": {"$ifNull": ["$created_at", {"$literal": now}]},
                }},
                {"$set": {"state.slots": {"$ifNull": ["$state.slots", {}]}}},
                {"$set": {key: {"$literal": value} for key, value in fields.items()}},
            ],
            upsert=True,
        )

    async def apply_turn_analysis(self, user_id: str, a: "TurnAnalysis",
                                  last_bot_text: str) -> None:
        """Ghi THẲNG kết quả bộ phân tích vào `state.slots.{service,stated,asked}`
        của HÔM NAY — không gọi LLM, không nén gì. `a` đã qua `sanitize_analysis`
        trước khi tới đây — hàm này KHÔNG lọc lại nội dung, chỉ hợp nhất và
        chặn trần.

        CHỈ `$set` BA trường slots này (review Task 12 #6) — không đọc-sửa-ghi
        CẢ document như `_compact`/`set_state` làm: một lượt nén chạy song
        song giữa lúc đọc và lúc ghi ở đây sẽ bị ghi đè `summary`/
        `covers_until`, và `$set` nguyên document cũng giẫm lên `$inc` của
        `bump_state_failures`. Document/`state` CHƯA có (hay của hôm qua,
        hay `slots: null`) thì `_set_today_fields` khởi tạo trong cùng lệnh.
        """
        now = now_utc()
        state = await self.conversations.get_state(user_id)
        current = state.slots if state else None

        service = a.service or (current.service if current else None)

        merged_stated = list(current.stated) if current else []
        for item in a.stated:
            if item not in merged_stated:
                merged_stated.append(item)
        merged_stated = merged_stated[-MAX_STATED:]

        asked = list(current.asked) if current else []
        question = last_question(last_bot_text)
        if question:
            existing = next((x for x in asked if x.question == question), None)
            if existing is not None:
                existing.times += 1
                if a.answers_question:
                    existing.answered = True
            else:
                asked.append(AskedItem(question=question,
                                       answered=bool(a.answers_question), times=1))
            asked = asked[-MAX_ASKED:]

        await self._set_today_fields(user_id, {
            "state.slots.service": service,
            "state.slots.stated": merged_stated,
            "state.slots.asked": [item.model_dump() for item in asked],
            "state.updated_at": now,
        }, now)

    async def maybe_compact(self, user_id: str) -> bool:
        """Nén nếu đáng nén. Chạy nền sau khi lượt chat đã complete, nên KHÔNG
        BAO GIỜ ném — lỗi gì cũng log rồi trả False."""
        try:
            return await self._compact(user_id)
        except Exception as exc:
            logger.warning("state_failed", extra={"user_id": user_id, "error": str(exc)})
            return False

    async def _compact(self, user_id: str) -> bool:
        # Import trễ (không ở đầu file): `app.agents.booking_graph.prompts` đi
        # qua `__init__.py` của package, mà package đó (qua events→confirm→
        # analyzer) lại `import clean_free_text_item` NGƯỢC từ chính module
        # này — import thẳng ở đầu file là vòng import chết. `_compact` chỉ
        # chạy nền, sau khi mọi module đã nạp xong, nên an toàn.
        from app.agents.booking_graph.prompts import DIGEST_PROMPT

        state = await self.conversations.get_state(user_id)
        if state and state.failures >= MAX_FAILURES:
            logger.info("state_skipped", extra={"user_id": user_id, "reason": "fuse"})
            return False

        messages = await self._todays_messages(user_id)
        older, _recent = split_window(messages, state.covers_until if state else None)
        if not older or estimate_tokens(older) < COMPACT_THRESHOLD_TOKENS:
            return False

        previous_digest = state.digest if state else None
        # 400 ký tự/tin là chặn có chủ ý trên ĐẦU VÀO của lượt nén: một tin
        # khách viết rất dài bị cắt trước khi đưa vào prompt, để một tin
        # không nuốt hết chỗ của cả transcript.
        transcript = "\n".join(
            f"{'customer' if m.role == 'user' else 'salon'}: {single_line(m.content, 400) or ''}"
            for m in older
        )
        # `now` lấy DUY NHẤT một lần cho cả lượt nén: dòng "Today is ..." của
        # prompt, upcoming_for_user, sanitize_slots và to_local(now).date() của
        # bản ghi phải cùng một mốc. Gọi now_utc() nhiều lần mở cửa cho ca nửa
        # đêm: hai lời gọi rơi hai bên mốc 00:00 giờ VN thì day của bản ghi
        # lệch với day mà slots vừa được lọc theo, và prompt lại nói ngày thứ ba.
        now = now_utc()
        # Prompt BẮT BUỘC nói hôm nay là ngày nào, ở ngay dòng đầu (CONTEXT.md
        # bẫy #6): model được yêu cầu xuất `day` dạng YYYY-MM-DD, không có mốc
        # này thì nó neo vào mốc trong dữ liệu huấn luyện, ra ngày quá khứ, và
        # sanitize_slots lặng lẽ bỏ — `day` gần như không bao giờ sống sót.
        # Bản digest CŨ đưa lại vào prompt (cuốn chiếu) — rỗng thì in "(none)"
        # cho từng mục thay vì danh sách trống khó đọc.
        empty_digest = previous_digest or StateDigest()
        prompt = DIGEST_PROMPT.format(
            today=to_local(now).date().isoformat(),
            max_chars=BULLET_MAX_CHARS,
            wants_max=DIGEST_CAPS["wants"], settled_max=DIGEST_CAPS["settled"],
            declined_max=DIGEST_CAPS["declined"], constraints_max=DIGEST_CAPS["constraints"],
            open_max=DIGEST_CAPS["open"],
            service_max=NOTE_MAX,
            prev_wants="; ".join(empty_digest.wants) or "(none)",
            prev_settled="; ".join(empty_digest.settled) or "(none)",
            prev_declined="; ".join(empty_digest.declined) or "(none)",
            prev_constraints="; ".join(empty_digest.constraints) or "(none)",
            prev_open="; ".join(empty_digest.open) or "(none)",
            messages=transcript,
        )
        # tags KHÔNG chứa "respond": token của lượt nén không được lọt ra màn
        # hình khách (CONTEXT.md bẫy #8). streaming=False vì đầu ra là JSON.
        model = build_chat_model(tags=["state"], temperature=0.0, streaming=False)
        try:
            result: StateOutput = await asyncio.wait_for(
                model.with_structured_output(StateOutput).ainvoke(prompt),
                timeout=STATE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("state_llm_failed", extra={"user_id": user_id, "error": str(exc)})
            await self.conversations.bump_state_failures(user_id)
            return False

        cleaned = sanitize_digest(result.digest)
        if not any(getattr(cleaned, field) for field in DIGEST_CAPS):
            # Nén "thành công" mà digest rỗng hoàn toàn thì cũng là hỏng:
            # advance covers_until là mất luôn ~800 token ngữ cảnh mà không có
            # log. Coi như một lần hỏng để cầu chì đếm, và state cũ (nếu có)
            # giữ nguyên. Lưu ý: slots rỗng KHÔNG rơi vào nhánh này — tán gẫu
            # thì slots rỗng là kết quả đúng, chỉ digest rỗng mới tính là hỏng.
            logger.warning("state_empty", extra={"user_id": user_id})
            await self.conversations.bump_state_failures(user_id)
            return False
        digest = merge_digest(previous_digest, cleaned)
        quotes = select_customer_quotes(messages, older[-1].created_at)
        slots = sanitize_slots(
            result.slots,
            now=now,
            user_id=user_id,
        )

        # CHỈ các trường lượt nén sở hữu (Task 12b) — `set_state` ghi CẢ
        # `state` và xoá `stated`/`asked`/`told`/`agreed_at` mà bộ phân tích
        # (`apply_turn_analysis`) đã ghi giữa hai lượt nén. `service` cả hai
        # bên cùng ghi: nén chỉ được ghi đè khi nó CÓ giá trị — nén không thấy
        # dịch vụ không có nghĩa là khách chưa nói.
        today = to_local(now).date()
        fields = {
            "state.day": datetime(today.year, today.month, today.day),
            "state.covers_until": older[-1].created_at,
            "state.digest": digest.model_dump(),
            "state.customer_quotes": quotes,
            "state.updated_at": now,
            "state.failures": 0,
            "state.slots.intent": slots.intent if slots else None,
            "state.slots.day": slots.day if slots else None,
            "state.slots.time": slots.time if slots else None,
            "state.slots.declined": list(slots.declined) if slots else [],
        }
        if slots and slots.service:
            fields["state.slots.service"] = slots.service
        await self._set_today_fields(user_id, fields, now)
        logger.info("state_compacted", extra={
            "user_id": user_id, "compacted": len(older), "quotes": len(quotes),
            "slots": bool(slots),
        })
        return True


ALLOWED_INTENTS = {"book", "reschedule", "cancel"}
MAX_DECLINED = 6
# Trần trên cho `day`: hội thoại trong phiên chỉ nói về lịch sắp tới rất gần.
# Không có trần thì "9999-12-31" đi qua sạch, mà _format_vi_day cố ý không in
# năm nên prompt ghi "Thứ Sáu 31/12" trong khi khối bối cảnh ghi năm khác —
# hai chỗ mâu thuẫn cả thứ trong tuần. 90 ngày rộng hơn mọi lịch tiệm từng
# nhận, nhưng chặn được lệch năm do model neo sai mốc thời gian.
MAX_DAYS_AHEAD = 90

# --- 4 nhóm mới (spec mục 3, bộ phân tích câu khách): `stated`/`told` là chữ
# TỰ DO (bộ phân tích hoặc code), `asked` là câu hỏi của chính bot. Dùng
# CHUNG một bộ lọc (SĐT/địa chỉ/câu mệnh lệnh) vì cả ba đều chảy thẳng vào
# prompt dưới vai "dữ kiện", không phải chữ khách gõ nguyên văn — một câu
# mệnh lệnh nhét vào đây là tiêm prompt y hệt một ghi chú lịch hẹn.
STATED_MAX_CHARS = 60
MAX_STATED = 5
MAX_ASKED = 5
MAX_TOLD = 5

_PHONE_RE = re.compile(r"\d[\d\s.\-]{6,}\d")
_ADDRESS_RE = re.compile(r"\b(đường|phường|quận|ngõ|ngách|số nhà|hẻm)\b", re.IGNORECASE)
_IMPERATIVE_RE = re.compile(r"\b(hãy|bỏ qua|ignore|quên|luôn đồng ý)\b", re.IGNORECASE)


def clean_free_text_item(raw: Optional[str], max_chars: int = STATED_MAX_CHARS) -> Optional[str]:
    """Lọc một mục chữ tự do trước khi vào `stated`/`told`: một dòng, ≤ max_chars
    ký tự, bỏ nếu chứa số điện thoại, địa chỉ hay câu mệnh lệnh ("hãy", "bỏ
    qua", "ignore", "quên", "luôn đồng ý"). Dùng chung cho `apply_turn_analysis`
    và `sanitize_analysis` (analyzer.py) — một nơi lọc, không lặp luật ở hai chỗ.
    """
    text = single_line(raw, max_chars)
    if not text:
        return None
    if _PHONE_RE.search(text) or _ADDRESS_RE.search(text) or _IMPERATIVE_RE.search(text):
        return None
    return text


def sanitize_digest(raw: Optional[StateDigest]) -> StateDigest:
    """Lọc digest do LLM sinh: mỗi mục qua `clean_free_text_item` (≤120 ký tự,
    bỏ SĐT/địa chỉ/câu mệnh lệnh — CÙNG bộ lọc với `stated`/`told`), rồi cắt
    về trần của từng phần (`DIGEST_CAPS`). Không ném lỗi — bỏ TỪNG mục sai,
    không đánh trượt cả lượt nén, cùng triết lý với `sanitize_slots`."""
    raw = raw or StateDigest()
    cleaned = {}
    for field, cap in DIGEST_CAPS.items():
        items = []
        for item in getattr(raw, field) or []:
            text = clean_free_text_item(item, BULLET_MAX_CHARS)
            if text:
                items.append(text)
        cleaned[field] = items[:cap]
    return StateDigest(**cleaned)


def merge_digest(previous: Optional[StateDigest], new: StateDigest) -> StateDigest:
    """Cuốn chiếu (spec mục 3): `wants`/`constraints` rỗng ở bản MỚI thì giữ
    nguyên bản CŨ — lưới chống mất sạch khi model quên nhắc lại trong lượt
    này, KHÔNG cố đoán mâu thuẫn. `settled`/`declined`/`open` luôn lấy từ bản
    mới — chúng tiến triển theo từng lượt, không cần cuốn chiếu.

    `new` đã qua `sanitize_digest` trước khi tới đây; `previous` lấy từ state
    đã lưu (cũng đã sạch) nên chỉ cắt lại trần cho chắc, không lọc lại nội
    dung."""
    previous = previous or StateDigest()
    wants = new.wants or list(previous.wants)
    constraints = new.constraints or list(previous.constraints)
    return StateDigest(
        wants=wants[:DIGEST_CAPS["wants"]],
        settled=list(new.settled)[:DIGEST_CAPS["settled"]],
        declined=list(new.declined)[:DIGEST_CAPS["declined"]],
        constraints=constraints[:DIGEST_CAPS["constraints"]],
        open=list(new.open)[:DIGEST_CAPS["open"]],
    )


def select_customer_quotes(
    session_messages: Sequence[ChatMessage], kept_from: datetime,
) -> List[str]:
    """Câu gốc của khách do CODE chọn, không LLM (spec mục 3): câu khách ĐẦU
    TIÊN của cả phiên, cộng 2 câu khách GẦN NHẤT nằm trong phần bị nén (tạo
    lúc hoặc trước `kept_from` — mốc `covers_until` mới). Bỏ trùng, bỏ câu
    khớp số điện thoại, mỗi câu `single_line(…, 120)`, tối đa 3 câu."""
    customers = [m for m in session_messages if m.role == "user"]
    if not customers:
        return []
    compacted = [m for m in customers if m.created_at <= kept_from]
    chosen = [customers[0], *compacted[-2:]]

    quotes: List[str] = []
    for m in chosen:
        text = single_line(m.content, BULLET_MAX_CHARS)
        if not text or _PHONE_RE.search(text) or text in quotes:
            continue
        quotes.append(text)
    return quotes[:MAX_CUSTOMER_QUOTES]


# Câu bot vừa hỏi: lấy CÂU CUỐI kết thúc bằng "?" trong `last_bot_text`, không
# phải 60 ký tự đầu của cả câu trả lời — review Task 12 #5: "Dạ chiều mai còn
# 3 giờ và 4 giờ ạ. Anh chị muốn giờ nào ạ?" trước đây lưu nhầm phần mở đầu
# ("Dạ chiều mai còn 3 giờ và 4..."), không phải câu hỏi thật.
_SENTENCE_SPLIT = re.compile(r"(?<=[.?!])\s+")


def last_question(text: Optional[str]) -> Optional[str]:
    """Câu hỏi CUỐI CÙNG trong một câu trả lời của bot, hoặc None nếu câu trả
    lời không kết ở một câu hỏi (vd. kết bằng dấu chấm, hoặc rỗng)."""
    if not text:
        return None
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text.strip()) if p.strip()]
    for part in reversed(parts):
        if part.endswith("?"):
            return single_line(part, STATED_MAX_CHARS)
    return None


def _drop(user_id: str, field: str, value) -> None:
    logger.info("state_slot_dropped", extra={
        "user_id": user_id, "field": field, "value": single_line(str(value), 40),
    })


def sanitize_slots(
    raw: Optional[ConversationSlots],
    *,
    now: datetime,
    user_id: str = "",
) -> Optional[ConversationSlots]:
    """Lọc slots do LLM sinh. Bỏ TỪNG field sai, không đánh trượt cả lượt nén.

    Thuần: `now` (UTC) truyền vào, không tự gọi DB hay đồng hồ —
    nhờ vậy test được mọi mốc thời gian mà không phải giả lập clock.
    """
    if raw is None:
        return None

    local_now = to_local(now)

    intent = raw.intent if raw.intent in ALLOWED_INTENTS else None
    if raw.intent and intent is None:
        _drop(user_id, "intent", raw.intent)

    service = single_line(raw.service, NOTE_MAX) or None
    if raw.service and not service:
        _drop(user_id, "service", raw.service)

    day: Optional[date] = None
    if raw.day:
        try:
            day = date.fromisoformat(raw.day)
        except ValueError:
            _drop(user_id, "day", raw.day)
        else:
            if day < local_now.date() or day > local_now.date() + timedelta(days=MAX_DAYS_AHEAD):
                _drop(user_id, "day", raw.day)
                day = None

    clock: Optional[dtime] = None
    if raw.time:
        try:
            clock = datetime.strptime(raw.time, "%H:%M").time()
        except ValueError:
            _drop(user_id, "time", raw.time)
        else:
            # Chỉ bỏ khi chắc chắn đã qua: cùng ngày hôm nay và giờ đã trôi.
            # Không có ngày thì không suy ra được, giữ lại.
            if day == local_now.date() and clock <= local_now.time():
                _drop(user_id, "time", raw.time)
                clock = None
            elif raw.day and day is None:
                # Model CÓ nói ngày nhưng ngày đó bị loại: giờ trơ lại một
                # mình sẽ được in là "Giờ đang nhắm: 9 giờ sáng" và model đọc
                # thành 9 giờ sáng HÔM NAY — sai hẳn ngày khách nhắm. Khách
                # chưa nói ngày (raw.day rỗng) thì vẫn giữ giờ như cũ.
                _drop(user_id, "time", raw.time)
                clock = None

    declined: List[str] = []
    for item in raw.declined or []:
        text = item.strip() if isinstance(item, str) else ""
        # Chỉ-có-ngày ("2026-09-20") vẫn qua được fromisoformat nhưng nó KHÔNG
        # phải mốc giờ khách đã lắc: render sẽ bịa ra 00:00 rồi đọc thành một
        # giờ tiệm chưa từng chào. Coi là rác.
        if ":" not in text:
            _drop(user_id, "declined", item)
            continue
        try:
            moment = datetime.fromisoformat(text)
        except (ValueError, TypeError):
            _drop(user_id, "declined", item)
            continue
        # CHUẨN HOÁ chứ không chỉ kiểm: lưu chuỗi thô là để naive datetime
        # trôi tới render_slots, nơi astimezone() lấy múi giờ của TIẾN TRÌNH —
        # xanh trên máy dev (TZ=+07) nhưng lệch 7 tiếng trong container UTC.
        # ensure_aware() là chỗ duy nhất được phép diễn giải naive (clock.py).
        moment = to_local(ensure_aware(moment))
        if moment.date() < local_now.date():
            # Hội thoại vắt từ hôm qua sang hôm nay: giờ đã qua thì in ra chỉ
            # chiếm chỗ và gây nhiễu, cùng luật với `day`.
            _drop(user_id, "declined", item)
            continue
        declined.append(moment.isoformat())
    declined = declined[-MAX_DECLINED:]

    # `stated`/`asked`/`told`/`agreed_at` KHÔNG BAO GIỜ đến từ lượt nén LLM
    # (review Task 12 #3 / design §3): chúng là của bộ phân tích + code
    # (`apply_turn_analysis`), ghi thẳng vào `state.slots` ở một đường hoàn
    # toàn khác, không qua `sanitize_slots`. `StateOutput.slots` vẫn khai báo
    # kiểu `ConversationSlots` đầy đủ (schema `with_structured_output` thấy
    # được 4 trường này), nên model CÓ THỂ bịa ra giá trị — luôn bỏ, không lọc
    # giữ lại, để không ghi đè lên dữ kiện bộ phân tích đã ghi trước lượt nén.
    for field in ("stated", "asked", "told", "agreed_at"):
        if getattr(raw, field):
            _drop(user_id, field, getattr(raw, field))

    cleaned = ConversationSlots(
        intent=intent,
        service=service,
        day=day.isoformat() if day else None,
        time=clock.strftime("%H:%M") if clock else None,
        declined=declined,
    )
    # Rỗng hoàn toàn thì trả None: khối slots sẽ không được in ra, và lượt nén
    # KHÔNG bị tính là hỏng — hội thoại tán gẫu thì rỗng mới là đúng.
    if cleaned == ConversationSlots():
        return None
    return cleaned
