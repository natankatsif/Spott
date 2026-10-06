from crawler.site import under_prefix


def test_prefix_is_matched_by_path_segments():
    assert under_prefix("/ro/servicii", "/ro/servicii")
    assert under_prefix("/ro/servicii/", "/ro/servicii")
    assert under_prefix("/ro/servicii/taxe", "/ro/servicii/")
    assert not under_prefix("/ro/servicii-noi", "/ro/servicii")
    assert under_prefix("/anything", "")  # a site-wide crawl
    assert under_prefix("/anything", "/")
