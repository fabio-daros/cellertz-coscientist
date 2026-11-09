import pytest

from cellertz.conversation.__main__ import conversation_port


def test_conversation_port_keeps_the_active_installation_default() -> None:
    assert conversation_port({}) == 8765
    assert conversation_port({"CELLERTZ_CONVERSATION_PORT": "   "}) == 8765


def test_conversation_port_accepts_an_explicit_port() -> None:
    assert conversation_port({"CELLERTZ_CONVERSATION_PORT": "8876"}) == 8876


@pytest.mark.parametrize("raw", ["0", "65536", "abc", "08765", "-1", "8765x"])
def test_conversation_port_rejects_an_invalid_value(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid_conversation_port"):
        conversation_port({"CELLERTZ_CONVERSATION_PORT": raw})
