"""Gói benchmark hộp đen cho salona-booking.

Mọi module trong `bench/` chỉ được nói chuyện với app đang chạy qua HTTP,
Socket.IO, và đọc trực tiếp Mongo để kiểm tra kết quả — KHÔNG BAO GIỜ
`import app` hay `from app ...`. Xem `bench/tests/test_fence.py`.
"""
