from app.agents.booking.phrase import code_phrase


class TestCodePhrase:
    """`code_phrase`: câu chốt đường nút, viết bằng CODE — không gọi LLM."""

    def test_code_phrase_avoids_a_template_already_used(self):
        fact = {"kind": "booked", "when": "Thứ Tư 21/10, 3 giờ chiều", "address": "chị Lan",
                "context": "first_time"}
        first = code_phrase(fact, [])
        second = code_phrase(fact, [first])
        assert first != second and "3 giờ chiều" in second and "chị Lan" in second

    def test_code_phrase_failed_uses_fallback(self):
        assert code_phrase({"kind": "failed", "fallback": "X"}, []) == "X"

    def test_code_phrase_defaults_missing_context_to_first_time(self):
        fact = {"kind": "booked", "when": "Thứ Tư 21/10, 3 giờ chiều", "address": "chị Lan"}
        with_context = code_phrase({**fact, "context": "first_time"}, [])
        without_context = code_phrase(fact, [])
        assert without_context == with_context

    def test_code_phrase_runs_out_of_templates_falls_back_to_first(self):
        fact = {"kind": "booked", "when": "Thứ Tư 21/10, 3 giờ chiều", "address": "chị Lan",
                "context": "first_time"}
        first = code_phrase(fact, [])
        second = code_phrase(fact, [first])
        third = code_phrase(fact, [first, second])
        # Cả 3 mẫu đều đã "dùng" (coi như trùng) — hết mẫu rơi về mẫu đầu.
        exhausted = code_phrase(fact, [first, second, third])
        assert exhausted == first
