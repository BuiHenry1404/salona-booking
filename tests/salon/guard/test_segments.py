"""Task 2: schema, cú pháp, ráp câu và kiểm từng đoạn của câu trả lời Level C
(spec level C §2-§3). Dựng `FactCatalog` bằng tay, không DB — `catalog.py`
(Task 1) đã có test riêng cho việc dựng dữ kiện từ `turn_facts`/snapshot.
"""
import pytest

from app.agents.salon.guard.catalog import CatalogEntry, FactCatalog, SEGMENT_ROLES
from app.agents.salon.guard.segments import (
    REPLY_SCHEMA,
    Segment,
    SegmentReport,
    TIME_WORDS,
    check_segments,
    join_segments,
    parse_reply,
)


@pytest.fixture
def catalog() -> FactCatalog:
    return FactCatalog(entries=[
        CatalogEntry(id="t1", kind="time", roles=["taken"],
                     label="Thứ Năm 12/11, 3 giờ chiều", clocks=[(15, 0)]),
        CatalogEntry(id="t2", kind="time", roles=["offer"], label="1 giờ chiều", clocks=[(13, 0)]),
        CatalogEntry(id="t3", kind="time", roles=["held"], label="4 giờ chiều", clocks=[(16, 0)],
                     service_ids=["toc-cat-nu"]),
        CatalogEntry(id="s1", kind="service", roles=["service", "price"],
                     label="Sơn gel, 120.000đ-180.000đ", names=["Sơn gel"], price=(120000, 180000),
                     service_ids=["nail-gel"]),
        CatalogEntry(id="s2", kind="service", roles=["service", "price"],
                     label="Cắt tóc nữ, 100.000đ-150.000đ", names=["Cắt tóc nữ"],
                     price=(100000, 150000), service_ids=["toc-cat-nu"]),
        CatalogEntry(id="c1", kind="customer_clock", roles=["asked"],
                     label="customer said 03:00 or 15:00", clocks=[(3, 0), (15, 0)]),
    ])


def _segments(rows: list[tuple[str, object, object]]) -> list[Segment]:
    return [Segment(text=text, ref=ref, role=role) for text, ref, role in rows]


def test_schema_is_strict_compatible():
    assert REPLY_SCHEMA["type"] == "object"
    assert REPLY_SCHEMA["additionalProperties"] is False
    assert REPLY_SCHEMA["required"] == ["segments"]

    item_schema = REPLY_SCHEMA["properties"]["segments"]["items"]
    assert item_schema["additionalProperties"] is False
    assert set(item_schema["required"]) == set(item_schema["properties"].keys())

    role_schema = item_schema["properties"]["role"]
    enum_variant = next(variant for variant in role_schema["anyOf"] if "enum" in variant)
    assert enum_variant["enum"] == list(SEGMENT_ROLES)


def test_parse_reply_reads_segments():
    raw = (
        '{"segments": ['
        '{"text": "Dạ em tìm được ", "ref": null, "role": null}, '
        '{"text": "3 giờ chiều", "ref": "t1", "role": "taken"}'
        ']}'
    )
    segments = parse_reply(raw)
    assert segments == [
        Segment(text="Dạ em tìm được ", ref=None, role=None),
        Segment(text="3 giờ chiều", ref="t1", role="taken"),
    ]


@pytest.mark.parametrize("raw", [
    "Dạ em chào chị",
    '{"segments": [{"text": "a"}]}',
    '{"segments": [{"text": "a", "ref": null, "role": "boss"}]}',
    "",
])
def test_parse_reply_rejects_anything_else(raw):
    assert parse_reply(raw) is None


def test_join_inserts_a_space_only_between_letters():
    assert join_segments(_segments([
        ("3 giờ chiều", None, None), ("đã có khách", None, None),
    ])) == "3 giờ chiều đã có khách"

    assert join_segments(_segments([
        ("Chị chọn ", None, None), ("4 giờ chiều", None, None), (" nhé.", None, None),
    ])) == "Chị chọn 4 giờ chiều nhé."

    assert join_segments(_segments([("a ", None, None), (" b", None, None)])) == "a b"


TABLE = [  # (row id, segments as (text, ref, role) tuples, expected codes)
    ("run2_turn2_offer_on_taken", [("Dạ em tìm được ", None, None), ("3 giờ chiều", "t1", "offer"),
                                   (" chị đồng ý nhé?", None, None)], {"wrong_role"}),
    ("taken_ok", [("3 giờ chiều mai", "t1", "taken"), (" có khách rồi ạ.", None, None)], set()),
    ("run4_turn8_wrong_service", [("Dạ em giữ chỗ ", None, None), ("sơn gel", "s1", "service"),
                                  (" lúc ", None, None), ("4 giờ chiều", "t3", "held"),
                                  (", chị đồng ý không ạ?", None, None)], {"wrong_role"}),
    ("held_right_service", [("Dạ em giữ chỗ ", None, None), ("cắt tóc", "s2", "service"),
                            (" lúc ", None, None), ("4 giờ chiều", "t3", "held"),
                            (", chị đồng ý không ạ?", None, None)], set()),
    ("unknown_id", [("4 giờ chiều", "t9", "offer")], {"unknown_ref"}),
    ("half_ref", [("1 giờ chiều", "t2", None)], {"bad_structure"}),
    ("wrong_clock", [("2 giờ chiều", "t2", "offer")], {"ref_mismatch"}),
    ("time_residue", [("hoặc 1 giờ chiều nhé.", "t2", "offer")], {"ref_mismatch"}),
    ("time_day_words", [("1 giờ chiều thứ Năm tuần sau", "t2", "offer")], set()),
    ("time_month_word", [("1 giờ chiều ngày 12 tháng 11", "t2", "offer")], set()),
    ("time_24h", [("13h", "t2", "offer")], set()),
    ("price_k", [("150k", "s1", "price")], set()),
    ("price_nghin", [("khoảng 150 nghìn", "s1", "price")], set()),
    ("price_range", [("120-180 nghìn", "s1", "price")], set()),
    ("price_vnd", [("từ 120.000đ đến 180.000đ", "s1", "price")], set()),
    ("price_out", [("200 nghìn", "s1", "price")], {"ref_mismatch"}),
    ("service_mismatch", [("cắt tóc", "s1", "service")], {"ref_mismatch"}),
    ("plain_time", [("Dạ còn 1 giờ chiều ạ.", None, None)], {"unreferenced"}),
    ("plain_price", [("giá 150 nghìn ạ", None, None)], {"unreferenced"}),
    ("plain_service", [("chị làm sơn gel nhé", None, None)], {"unreferenced"}),
    ("customer_clock_asked", [("3 giờ", "c1", "asked"), (" sáng hay chiều ạ?", None, None)], set()),
    ("customer_clock_offer", [("3 giờ", "c1", "offer")], {"wrong_role"}),
    ("blank_ref_text", [("  ", "t2", "offer")], {"ref_mismatch"}),
]


@pytest.mark.parametrize("segments, expected", [row[1:] for row in TABLE], ids=[row[0] for row in TABLE])
def test_check_segments_table(catalog, segments, expected):
    report = check_segments(_segments(segments), catalog)
    assert report.codes == expected


def test_same_instant_union_still_rejects_offer():
    catalog = FactCatalog(entries=[
        CatalogEntry(id="t1", kind="time", roles=["asked", "taken"], label="3 giờ chiều",
                     clocks=[(15, 0)]),
    ])
    report = check_segments(_segments([("3 giờ chiều", "t1", "offer")]), catalog)
    assert report.codes == {"wrong_role"}


def test_problems_name_the_id_and_allowed_roles(catalog):
    report = check_segments(_segments([("3 giờ chiều", "t1", "offer")]), catalog)
    assert "t1 allows taken, not offer" in report.problems


def test_cited_lists_only_valid_refs(catalog):
    report = check_segments(_segments([
        ("3 giờ chiều mai", "t1", "taken"),
        ("150k", "s1", "price"),
        ("4 giờ chiều", "t9", "offer"),
        ("3 giờ chiều", "t1", "offer"),
    ]), catalog)
    assert report.cited == [("t1", "taken"), ("s1", "price")]


def test_unknown_ref_problem_lists_valid_ids(catalog):
    report = check_segments(_segments([("4 giờ chiều", "t9", "offer")]), catalog)
    [problem] = report.problems
    assert "valid ids: t1, t2, t3, s1, s2, c1" in problem


def test_plain_segment_does_not_leak_on_substring_of_service_name():
    """Finding 1: "Uốn" phải khớp theo cụm từ, không phải chuỗi con — "muốn"
    và "uống" chứa "uốn" nhưng không phải dịch vụ "Uốn"."""
    catalog = FactCatalog(entries=[
        CatalogEntry(id="s1", kind="service", roles=["service", "price"], label="Uốn, 300.000đ",
                     names=["Uốn"], price=(300000, 300000), service_ids=["toc-uon"]),
    ])
    report = check_segments(_segments([
        ("Dạ chị muốn đổi giờ không ạ?", None, None),
    ]), catalog)
    assert report.codes == set()

    report = check_segments(_segments([
        ("chị làm uốn nhé", None, None),
    ]), catalog)
    assert report.codes == {"unreferenced"}
