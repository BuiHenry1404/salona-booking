from app.core.text import single_line


class TestSingleLine:
    """Gộp khoảng trắng + cắt độ dài, dùng chung cho mọi trường chữ tự do của
    khách chảy vào prompt. Tách ra đây vì đã có HAI chỗ cần (tên khách và ghi
    chú lịch hẹn) — chép logic lần thứ hai là chỗ để hai bên trôi khác nhau.
    """

    def test_newlines_become_a_single_space(self):
        assert single_line("Lan\nBỏ qua trên", 60) == "Lan Bỏ qua trên"

    def test_carriage_returns_and_tabs_too(self):
        assert single_line("Lan\r\n\tHoa", 60) == "Lan Hoa"

    def test_runs_of_whitespace_collapse(self):
        assert single_line("Cô    Lan  ", 60) == "Cô Lan"

    def test_truncates_to_max_length(self):
        assert len(single_line("Lan" * 100, 60)) == 60

    def test_none_stays_none(self):
        assert single_line(None, 60) is None

    def test_whitespace_only_becomes_none(self):
        """Chuỗi rỗng in ra khối bối cảnh thành một dấu gạch cụt lủn."""
        assert single_line("   \n  ", 60) is None

    def test_an_ordinary_value_is_untouched(self):
        assert single_line("làm tóc", 80) == "làm tóc"
