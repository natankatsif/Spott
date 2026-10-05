from spott.ingest.common.paths import MAX_NAME_LEN, safe_filename

WINDOWS_FORBIDDEN = set('<>:"/\\|?*')


def test_plain_name_unchanged():
    assert safe_filename("file_autosalubritate.md_report.pdf") == "file_autosalubritate.md_report.pdf"


def test_forbidden_characters_replaced():
    name = safe_filename('file:site.md/a?b=1&c="x"|<y>*z\\w')
    assert not WINDOWS_FORBIDDEN & set(name)


def test_no_trailing_dot_or_space():
    assert not safe_filename("page:site.md/dir. ").endswith((".", " "))


def test_long_name_is_cut_and_stays_unique():
    base = "file:site.md/" + "x" * 300
    a, b = safe_filename(base + "a"), safe_filename(base + "b")
    assert len(a) <= MAX_NAME_LEN and len(b) <= MAX_NAME_LEN
    assert a != b


def test_deterministic():
    assert safe_filename("page:a/b" * 50) == safe_filename("page:a/b" * 50)
