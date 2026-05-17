from types import SimpleNamespace

from gateway.run import _prepend_reply_context


def test_reply_context_injection_preserves_full_text_without_500_char_truncation():
    reply_text = "A" * 500 + "TAIL_MUST_SURVIVE"
    event = SimpleNamespace(reply_to_message_id="42", reply_to_text=reply_text)

    result = _prepend_reply_context("user follow-up", event)

    assert result.startswith("[Reply context: full text of replied-to message 42]\n")
    assert reply_text in result
    assert "TAIL_MUST_SURVIVE" in result
    assert result.endswith("\n\nuser follow-up")


def test_reply_context_injection_noops_without_reply_text():
    event = SimpleNamespace(reply_to_message_id="42", reply_to_text=None)

    assert _prepend_reply_context("plain prompt", event) == "plain prompt"
