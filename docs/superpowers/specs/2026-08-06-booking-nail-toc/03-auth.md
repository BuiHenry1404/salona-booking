# Xác thực và tài khoản

Đăng nhập bằng SĐT và mật khẩu, trả về JWT. Không có đăng ký tự do — chỉ admin tạo tài khoản.

SĐT được chuẩn hóa về một dạng duy nhất trước khi lưu và trước khi tra cứu. `0912...`, `+8491...` và `84912...` phải quy về cùng một chuỗi, nếu không người lớn tuổi sẽ nhập mỗi lần một kiểu và không đăng nhập được.

Quên mật khẩu: nhập SĐT và mật khẩu mới. Có trong DB thì đổi, không có thì hiện "Không tìm thấy tài khoản với số này". Kèm rate limit và log — xem phần rủi ro đã chấp nhận trong [README](README.md).

Rate limit lưu trong Mongo (collection `rate_limits`, khóa là SĐT hoặc IP, có TTL index tự dọn sau 1 giờ) chứ không giữ trong bộ nhớ tiến trình — stack không có Redis, và biến trong RAM sẽ mất khi restart cũng như không dùng được khi chạy nhiều worker.

`role` quyết định vào giao diện khách hay chủ tiệm. Route của admin được chặn ở backend, không chỉ ẩn trên React.

