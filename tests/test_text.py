from app.core.text import detect_self_address, single_line


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


class TestDetectSelfAddress:
    """Task 13 interface, dựng ở Task 12 vì sanitize_analysis cần nó. Khách
    lớn tuổi tự xưng bằng chính cách họ muốn được gọi — "chị đặt lịch" là
    CHÍNH khách muốn đặt, không phải đang nói về người khác."""

    def test_self_address_chi_mid_sentence(self):
        assert detect_self_address("mai 3 giờ chị làm móng") == "chị"

    def test_self_address_anh_via_cho(self):
        assert detect_self_address("cho anh 3 giờ chiều") == "anh"

    def test_em_is_not_self_address(self):
        assert detect_self_address("em muốn làm tóc") is None

    def test_third_person_capitalised_name_is_not_self_address(self):
        assert detect_self_address("chị Hoa bạn em làm được không") is None

    def test_anh_chi_together_is_not_self_address(self):
        assert detect_self_address("anh chị ơi") is None

    def test_unaccented_chi(self):
        assert detect_self_address("chi lam mong") == "chị"

    def test_unaccented_anh_via_cho(self):
        assert detect_self_address("cho anh 3h") == "anh"

    # Review Task 12 #2: "em"/"con"/"bé" là người đang NÓI — chị/anh trong
    # câu là người được nhắc tới (lễ tân hoặc người thứ ba), không phải tự
    # xưng. "giúp/cho chị/anh TÊN" là nhờ vả CHO người thứ ba.
    def test_em_asking_about_chi_is_not_self_address(self):
        assert detect_self_address("em muốn hỏi chị làm được không") is None

    def test_em_asking_about_anh_is_not_self_address(self):
        assert detect_self_address("em hỏi anh rảnh không") is None

    def test_con_is_not_self_address_even_with_chi(self):
        assert detect_self_address("con muốn chị làm móng cho con") is None

    def test_be_is_not_self_address_even_with_chi(self):
        assert detect_self_address("bé muốn chị làm cho nó") is None

    def test_giup_chi_name_is_a_third_person_favour(self):
        assert detect_self_address("giúp chị hoa đặt lịch") is None

    def test_cho_chi_name_is_a_third_person_favour(self):
        assert detect_self_address("cho chị lan làm móng") is None

    def test_mixed_accents_chi_lam_mong(self):
        assert detect_self_address("chị lam mong") == "chị"

    def test_chi_with_the_di_verb(self):
        assert detect_self_address("chị đi làm tóc") == "chị"
