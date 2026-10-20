"""Middleware cho các agent `create_agent` (shop / social / booking).

Mỗi middleware không giữ trạng thái lượt chat trong `self.*`: mọi thứ theo lượt
đi qua state hoặc request.
"""
