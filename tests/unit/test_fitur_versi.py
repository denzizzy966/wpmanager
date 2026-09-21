from types import SimpleNamespace

import pytest

from wpmgr.fitur import EVENTS, SELF_UPDATE, TRAFFIC, punya_fitur
from wpmgr.versi import lebih_lama


def test_punya_fitur():
    site = SimpleNamespace(fitur=[EVENTS, SELF_UPDATE])
    assert punya_fitur(site, EVENTS)
    assert not punya_fitur(site, TRAFFIC)
    assert not punya_fitur(SimpleNamespace(fitur=None), EVENTS)


@pytest.mark.parametrize(
    ("a", "b", "harapan"),
    [
        ("1.0.0", "2.0.0", True),
        ("2.0.0", "2.0.0", False),
        ("2.0.1", "2.0.0", False),
        ("2.0.0", "2.0.1-uji", True),
        ("2.0.1-uji", "2.0.1", False),
        ("2.0", "2.0.1", True),
        (None, "2.0.0", False),
        ("2.0.0", None, False),
        ("abc", "2.0.0", False),
    ],
)
def test_lebih_lama(a, b, harapan):
    assert lebih_lama(a, b) is harapan
