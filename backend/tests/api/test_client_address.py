"""Rate limits count the real client: X-Forwarded-For is believed only from a proxy on a private network, and
then its last hop (the one our proxy added), since the client writes the rest."""

from types import SimpleNamespace

from spott.api.answering.claims import copied_from
from spott.api.errors import RateLimiter, client_address


def request(peer: str, forwarded: str | None = None):
    headers = {"x-forwarded-for": forwarded} if forwarded is not None else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_a_direct_client_is_counted_by_its_own_address_whatever_it_sends():
    assert client_address(request("8.8.8.8", "1.2.3.4")) == "8.8.8.8"
    assert client_address(request("8.8.8.8")) == "8.8.8.8"


def test_behind_our_proxy_the_last_hop_counts_not_the_one_the_client_wrote():
    assert client_address(request("172.18.0.5", "6.6.6.6, 1.1.1.1")) == "1.1.1.1"
    assert client_address(request("127.0.0.1", "1.1.1.1")) == "1.1.1.1"
    assert client_address(request("172.18.0.5")) == "172.18.0.5"  # a local call with no header


def test_the_limiter_forgets_idle_clients():
    limiter = RateLimiter(limit=5, window_s=0.0)
    limiter.MAX_CLIENTS = 3
    for i in range(10):
        limiter.check(f"c{i}")
    assert len(limiter.hits) <= 4


def test_an_address_without_numbers_is_checked_by_its_words():
    lines = ["Primăria municipiului Chișinău, bd. Ștefan cel Mare și Sfânt, 83"]
    assert copied_from("bd. Ștefan cel Mare și Sfânt, 83", lines)
    assert copied_from("bd. Stefan cel Mare", lines)  # diacritics aside
    assert not copied_from("str. Pușkin", lines)  # not in the lines: invented
    assert not copied_from("bd. Ștefan cel Mare 99", lines)  # a number that isn't there
