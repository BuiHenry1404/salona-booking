# T0 — Dọn bộ test trước khi viết lại (độc lập với M0–M6)

Đọc `README.md` mục **Chính sách test** trước. Hiện trạng (2026-10-30): 73 file, 1294 hàm test, 1468 ca thu thập (`pytest --co`), 16 976 dòng; chạy hết **146 giây**: 1417 xanh, 1 đỏ, 50 lỗi fixture (xem T0.0). 840 hàm (65%) thuộc tầng agent cũ; M6 xoá 16 file (≈400 hàm) và cắt 8 file nữa. T0 dọn **phần còn lại** và những thứ M6 không chạm: test ngoài tầng agent, test trùng, test ghim hằng số, test đọc chữ prompt/docstring. Mỗi task một commit; suite xanh sau mỗi task.

**Nguyên tắc khi xoá:** test chỉ bị xoá khi (1) nó kiểm *chữ* chứ không kiểm *hành vi* (substring trong prompt/docstring, giá trị hằng), hoặc (2) đã có test khác đỏ cùng lúc khi hành vi đó hỏng. Trước khi xoá một test hành vi, tìm test đỏ thay nó bằng cách tạm phá code (`git stash` sau khi xem).

---

### Task T0.0: Làm suite xanh lại trước khi dọn

**Hiện tượng:** 50 ERROR `TypeError: AsyncClient.__init__() got an unexpected keyword argument 'app'` ở mọi test dùng fixture `async_client` (`tests/conftest.py:53`: `test_api_flow`, `test_auth_cookies`, `test_admin_users_api`, một phần `test_notification_wiring`). Nguyên nhân: venv đang có `httpx 0.28.1`, `fastapi 0.116.1`, `starlette 0.47.2` trong khi `requirements.txt` ghim `httpx==0.25.2`, `fastapi==0.104.1`; `httpx ≥ 0.28` bỏ tham số `app=`. `uv.lock` trong working tree bị sửa (+1085/−2032 dòng) cùng gốc.
**Hiện tượng 2:** `tests/test_conversation_sessions.py::test_short_session_without_compaction_still_saves_episodic_memory_preserving_digest` đỏ vì ghim ngày cứng `"2026-10-29"`; `sanitize_slots` bỏ ngày đã qua nên từ 30/10 test này đỏ mãi.

- [ ] **Step 1 — chủ dự án đã quyết (30/10): nâng pin theo venv hiện tại.** `requirements.txt`: `fastapi==0.116.1`, `httpx==0.28.1`, thêm/đổi `starlette` nếu đang ghim; kiểm `bcrypt` vẫn `4.0.1`, `pydantic-settings==2.1.0` còn tương thích (`pip check` sạch). Commit `uv.lock` đang sửa cùng lúc. `tests/conftest.py` fixture `async_client` → `AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")` (httpx ≥ 0.27). `tests/test_requirements_pins.py` chạy lại để biết nó ghim gì.
- [ ] **Step 2:** sửa test ngày cứng: thay `"2026-10-29"` bằng ngày tính từ `today` (`to_local(now_utc()).date() + timedelta(days=1)`), đúng cách `test_conversation_slots.py::test_today_is_not_the_past` đang làm. Quét thêm: `grep -nE "\"20[0-9]{2}-[0-9]{2}-[0-9]{2}\"" tests/*.py` → mọi ngày cứng ở test **có** so với "hôm nay" phải đổi sang tương đối; ngày cứng ở test thuần (parse chuỗi, format) giữ.
- [ ] **Step 3:** `pytest -q` → 0 failed, 0 error. Commit `test: fix the httpx client fixture and date-pinned session test`

### Task T0.1: Gộp bản sao `single_line`

**Files:** `tests/test_user_model.py` (5 test trùng tên và trùng dữ liệu với `tests/test_text.py`: `test_newlines_become_a_single_space`, `test_carriage_returns_and_tabs_too`, `test_runs_of_whitespace_collapse`, `test_whitespace_only_becomes_none`, `test_none_stays_none`)

- [ ] Giữ **một** test ở `test_user_model.py`: `test_full_name_goes_through_single_line` (một assert `"Lan\r\n\tHoa" → "Lan Hoa"`); xoá 4 test còn lại. `test_text.py` giữ nguyên (nó kiểm hàm gốc).
- [ ] `pytest tests/test_user_model.py tests/test_text.py -q` xanh. Commit `test: drop duplicated single_line cases`

### Task T0.2: Xoá test ghim hằng số / chữ ký

**Files & test cần xoá:**
- `tests/test_timeparse.py::test_timeout_constant_kept_for_compat`
- `tests/test_socket_notifier.py::test_socket_notifier_implements_notifier_protocol` (mypy việc này; runtime đã có `test_created_goes_to_admins_with_full_payload`)
- `tests/test_tools.py::TestShopHoursTool::test_shop_tools_has_exactly_two_tools` (M0 đổi thành 3; thay bằng `test_tool_names_are_exactly_as_specified` đã có)
- `tests/test_guard.py::test_voice_rules_default_is_on` (sẽ mất ở M6 cùng cờ `guard_voice_rules`)
- ba test `*_module_uses_zero_regex` trong `test_tools.py`, `test_reply_veto.py`, `test_relatives.py` — `tests/test_regex_budget.py` đã kiểm cả thư mục

- [ ] Xoá 7 test. `pytest -q` xanh. Commit `test: remove constant-pinning tests covered elsewhere`

### Task T0.3: Gộp các biến thể "notifier hỏng không làm hỏng nghiệp vụ"

**Files:** `tests/test_notification_wiring.py` (14 test), `tests/test_telegram_notifier.py` (5), `tests/test_notifications.py` (6)

- [ ] `test_notification_wiring.py`: 4 test `test_broken_notifier_does_not_break_{booking,cancelling,set_busy,set_free}` → **một** test parametrize theo `(hành động, hàm gọi)`; 2 test `test_set_busy_fires_status_changed`/`test_set_free_fires_status_changed` → một parametrize. Kết quả 14 → 10 hàm, số ca giữ nguyên.
- [ ] `test_telegram_notifier.py::test_telegram_failure_does_not_break_booking` trùng ý với dòng parametrize trên (khác chỗ hỏng) → giữ, vì nó kiểm tầng Telegram thật.
- [ ] `pytest tests/test_notification*.py tests/test_telegram_notifier.py -q` xanh. Commit `test: parametrize broken-notifier variants`

### Task T0.4: Prompt — bỏ test đọc chữ, giữ bất biến

**Files:** `tests/test_conversation_state_service.py` (3 test `test_the_prompt_asks_for_slots`, `test_the_prompt_says_what_day_today_is`, `test_the_prompt_promises_the_same_cap_the_code_enforces`), `tests/test_prompts.py` nhóm `TestDigestPromptIsEnglishWithNoVietnameseSamples` (7 test)

Đây là phần **sống sót qua M6** (DIGEST_PROMPT giữ). Phần còn lại của `test_prompts.py` (74 test về SUPERVISOR/BOOKING/SHOP/SOCIAL/ANALYZE/REWRITE) M6 xoá cùng prompt, **không** dọn ở đây để khỏi làm hai lần.

- [ ] Thay 10 test trên bằng **2** test trong `tests/test_prompts.py`: `test_digest_prompt_is_english_with_no_vietnamese_samples` (không ký tự có dấu; `splitlines()[0].startswith("Today is {today}")`) và `test_digest_prompt_names_every_cap_the_code_enforces` (parametrize 5 khoá `DIGEST_CAPS` xuất hiện trong prompt đã `.format`). Các test "mentions all five sections", "previous digest rolled forward", "wants kept unless contradicted", "withdrawal rule" là kiểm chữ của rule → xoá; hành vi tương ứng đã có ở `test_conversation_state_service.py` (`test_merge_*`, `test_withdrawal_sticks_*`, `test_previous_digest_is_passed_*`).
- [ ] `pytest tests/test_prompts.py tests/test_conversation_state_service.py -q` xanh. Commit `test: keep only invariant checks on the digest prompt`

### Task T0.5: `test_tools.py` — bỏ test docstring

**Files:** `tests/test_tools.py` (29 test đọc `description`/`__doc__`: `TestRulesMovedIntoToolDescriptions` (4), `TestCancelKnowsWhereTheIdLives`, `TestCancelDescriptionSaysCallFirst`, `TestNoteIsTheServiceOnly::test_propose_description_forbids_time_words_in_note`, `TestProposeCanReplaceAnExistingAppointment::test_the_description_tells_the_model_when_to_pass_it`, `TestCancelGoesThroughConfirm::test_the_description_says_the_customer_confirms_next_turn`, `TestParseTimeAnchor::test_description_explains_the_anchor`, `test_free_slots_docstring_says_at_most_three`, `test_propose_appointment_has_no_xung_ho_parameter`, …)

Tool giữ qua M6 (dời sang `booking/tools.py`), nên dọn ở đây có giá trị lâu dài.

- [ ] Giữ **một** test bất biến `test_tool_descriptions_are_english` (đã có). Xoá mọi test khác có assert trên `.description`/`__doc__` (liệt kê bằng `grep -n "description\|__doc__" tests/test_tools.py`). Giữ toàn bộ test hành vi (JSON trả về, DB, mã lỗi, `nearest`, `queued`, anchor).
- [ ] `pytest tests/test_tools.py -q` xanh; ghi số hàm trước/sau vào commit. Commit `test(tools): test behaviour, not docstrings`

### Task T0.6: Ghi lại số liệu

- [ ] `pytest --co -q | tail -1` và `grep -cE "def test_" tests/test_*.py | ...` → ghi vào bảng dưới và vào mô tả PR.

| Mốc | Hàm test | Ca thu thập |
|---|---|---|
| Trước T0 | 1294 | 1468 |
| Sau T0 | 1261 | 1443 |
| Mục tiêu sau M6 | ≈ 850 | ≈ 1000 |
