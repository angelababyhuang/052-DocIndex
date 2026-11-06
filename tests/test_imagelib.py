"""图片引用写法与编号解析（#21 回指用到的纯函数）。"""
from docarchive import imagelib


def test_image_ref_roundtrips_through_ref_codes():
    ref = imagelib.image_ref("IMG-0012")
    assert ref == "{{IMG-0012}}"
    assert imagelib.ref_codes(f"前 {ref} 后") == ("IMG-0012",)


def test_ref_codes_dedupes_and_keeps_order():
    text = "{{IMG-0002}} {{IMG-0001}} {{IMG-0002}}"
    assert imagelib.ref_codes(text) == ("IMG-0002", "IMG-0001")


def test_ref_codes_requires_braces_and_four_digits():
    assert imagelib.ref_codes("IMG-0001 裸写，{{IMG-1}} 不是合法编号") == ()
    assert imagelib.ref_codes("{{IMG-12345}}") == ("IMG-12345",)


def test_all_codes_reads_bare_codes():
    assert imagelib.all_codes("图片清单：IMG-0001, IMG-0002，重复 IMG-0001") == (
        "IMG-0001", "IMG-0002",
    )
    assert imagelib.all_codes("没有编号") == ()
