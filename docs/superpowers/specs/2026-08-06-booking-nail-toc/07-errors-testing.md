# Xử lý lỗi và kiểm thử

## Xử lý lỗi

Nguyên tắc: người dùng không bao giờ thấy lỗi kỹ thuật. Mọi lỗi được dịch thành một câu tiếng Việt kèm việc cần làm tiếp theo.

| Tình huống | Xử lý |
|---|---|
| Trùng giờ (unique index từ chối) | Không phải lỗi. Service bắt, AI trả lời "Giờ đó có người rồi ạ, 3h30 hoặc 4h được không cô?" kèm 2 giờ trống gần nhất |
| LLM lỗi hoặc timeout | "Máy đang bận chút xíu, cô nhắn lại giúp con nhé", hiện nút gọi điện cho tiệm |
| pgvector hoặc Mem0 chết | Fail-soft. `recall` trả rỗng, chat chạy bình thường, ghi log cảnh báo |
| Số chiều embedding lệch | App phát hiện lúc khởi động và log lỗi rõ ràng, thay vì để ghi memory hỏng âm thầm |
| AI gọi tool đặt lịch hai lần | Khóa idempotency trả về chính lịch đã tạo, không tạo trùng |
| Mongo chết | Fail-hard. Trả 503, màn hình hiện số điện thoại tiệm |
| AI hiểu sai giờ | Trước khi ghi, AI luôn nhắc lại để xác nhận: "Con đặt Thứ Năm 7/8, 3 giờ chiều, làm tóc — đúng không cô?" Chỉ ghi sau khi user xác nhận |
| Giờ trong quá khứ hoặc quá xa | Service từ chối ở tầng nghiệp vụ, không tin AI. AI diễn giải lại cho user |
| Mất mạng giữa chừng | React giữ tin nhắn chưa gửi, hiện "Đang gửi lại…", tự thử lại |
| Langfuse lỗi | Nuốt lỗi, không bao giờ ảnh hưởng lượt chat |
| Telegram lỗi hoặc token sai | Nuốt lỗi trong background task, ghi log. Đặt lịch vẫn thành công, admin vẫn thấy trên web |
| Bot nhận tin từ chat_id lạ | Bỏ qua im lặng, không trả lời gì |

Bước xác nhận trước khi ghi lịch là quan trọng nhất trong bảng này. Nó là lưới an toàn cho toàn bộ phần diễn giải ngôn ngữ tự nhiên, và cũng đúng thói quen người lớn tuổi vốn thích được nhắc lại.

## Kiểm thử

Ba tầng. Không tầng nào cần LLM thật, trừ tầng cuối.

**Unit — tầng service.** Quan trọng nhất, hoàn toàn không có AI. Chặn trùng giờ, bao gồm một test hai request đồng thời bằng `asyncio.gather` để chứng minh unique index thật sự chặn. Lượng tử hóa slot. Chuẩn hóa SĐT với đủ biến thể. Logic `busy_until` hết hạn tự về rảnh. Rate limit đổi mật khẩu. Quyền hủy lịch chỉ của chính mình. Khóa idempotency: gọi `create_appointment` hai lần cùng tham số chỉ sinh ra một lịch. `find_free_slots` không bao giờ trả mốc ngoài `shop_hours`, rơi vào `closed_days`, hoặc ở quá khứ.

Hai test bắt buộc cho partial index, vì đây là chỗ đã từng thiết kế sai: **hủy hai lịch liên tiếp không được ném lỗi trùng khóa** (đúng cái bug mảng rỗng ở [02-data-model.md](02-data-model.md#chặn-trùng-giờ)), và **hủy xong thì đặt lại đúng khung giờ đó phải thành công**.

**Tầng memory với Mem0 giả lập.** `recall` chỉ trả memory của đúng `user_id`; `recall` quá 2 giây thì trả rỗng; `remember` nuốt lỗi và không ném ra ngoài. Test quan trọng nhất, hoàn toàn không cần LLM: **memory của user A không bao giờ lọt sang user B**. Thêm một test cho khối bối cảnh — dựng state có memory chứa tên sai, khẳng định prompt vẫn mang tên lấy từ `users`.

**Tầng agent với LLM giả lập.** Mỗi node LangGraph test riêng bằng state dựng sẵn: supervisor định tuyến đúng nhánh, `refuse` chặn câu ngoài chủ đề, tool không nhận `user_id` từ nội dung tin nhắn, và `pending_confirmation` khiến câu "ừ" đi thẳng vào nhánh thực thi thay vì quay lại supervisor.

**Tầng Telegram — tất định, không cần LLM.** Update từ chat_id lạ bị bỏ qua và không sinh phản hồi nào. Bấm "Tôi đang bận 30 phút" ghi đúng `busy_until` **và** phát `shop_status_changed` qua Socket.IO. Telegram lỗi thì `create_appointment` vẫn thành công.

**Integration đầu-cuối, có LLM thật.** Chạy tay hoặc trong một job CI riêng. Bộ khoảng 15 câu tiếng Việt đời thường: "mai 3h chiều làm tóc được không con", "chú có rảnh giờ không", "hủy giùm cô cái lịch mai", "cháu bán bảo hiểm không". Kiểm tra kết quả cuối cùng chứ không so từng chữ.

Test hiện có phải viết lại vì đổi email sang SĐT và AutoGen sang LangGraph: `tests/test_auth.py`, `tests/test_chat.py`. `tests/test_youtube_search.py` xóa cùng `app/agents/soulcare_team.py`.

