"""Prompt cho lần viết lại DUY NHẤT của guard (`rewrite.py`, tag `rewrite`) —
dời nguyên văn từ `prompts.py` của gói cũ ở Task 6.1, kèm luật ngôn ngữ đầu ra
`_VIETNAMESE_ONLY` mà prompt này nhắc ở đầu và cuối.
"""

# Luật ngôn ngữ đặt riêng để không lọt: prompt tiếng Anh làm tăng khả năng model
# đáp bằng tiếng Anh. Với khách của tiệm thì đó là hỏng sản phẩm, không phải lỗi
# nhỏ. Nhắc ở ĐẦU và CUỐI mỗi prompt sinh câu cho khách.
_VIETNAMESE_ONLY = """OUTPUT LANGUAGE — ABSOLUTE:
Every word you say to the customer MUST be Vietnamese. Never answer in English,
never mix English words in. These instructions are English; your reply is not."""


# Mã vi phạm do guard.py phát hiện → một dòng giải thích cho rewrite. Tiếng
# Anh, không câu mẫu; "anh"/"chị" là chủ thể luật, không phải ví dụ.
VIOLATION_HINTS = {
    "repeat": "It repeats a sentence you already said in this conversation; say it differently.",
    "pronoun": "It addresses the customer with the wrong pronoun or the wrong register (a word for elders, or a child speaker); use exactly the form given below, in the em — anh/chị register.",
    "clock": "It writes a time with a colon or with number words; write digits followed by the part of the day, as the tools do. Never change a customer's name — only clock times and dates.",
    "content": "It dropped a date, time or number that the original contained; keep every one of them.",
    "claim": "It says a time is already held or booked although it is not; only the customer's appointments listed in the context block and the one you are holding this turn are held or booked. Say any other time is not held yet.",
    "ask_without_proposal": "Offer the time as a choice and ask which time suits, do not ask for a yes/no confirmation.",
}


REWRITE_PROMPT = f"""You are fixing ONE reply written by a salon receptionist to a customer.
Rewrite it in Vietnamese so that it says the same thing with the same dates,
times and numbers, but without the problems listed. Change wording only —
never add facts, never remove a date, time or number. Reply with the new
sentence only, nothing else.

{_VIETNAMESE_ONLY}

Problems:
{{violations}}

Address the customer as: {{address}}

Your previous replies to the customer (do not repeat any of them):
{{previous}}

Reply to fix:
{{draft}}

{_VIETNAMESE_ONLY}"""
