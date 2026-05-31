from apps.slash.parser import parse


def test_plain_text():
    assert parse("hello world") == ("text", "hello world")


def test_leading_whitespace_text_branch():
    assert parse("  hello world") == ("text", "  hello world")


def test_trailing_whitespace_text_branch():
    assert parse("hello world  ") == ("text", "hello world  ")


def test_stop_no_args():
    assert parse("/stop") == ("slash", "stop", [])


def test_model_single_arg():
    assert parse("/model gpt-4") == ("slash", "model", ["gpt-4"])


def test_model_quoted_arg():
    assert parse('/model "gpt 4"') == ("slash", "model", ["gpt 4"])


def test_account_label():
    assert parse("/account work") == ("slash", "account", ["work"])


def test_unknown_command():
    assert parse("/foobar a b") == ("slash", "foobar", ["a", "b"])


def test_empty_string():
    assert parse("") == ("text", "")


def test_whitespace_only():
    assert parse("   ") == ("text", "   ")


def test_slash_in_middle():
    assert parse("hello /world") == ("text", "hello /world")


def test_command_lowercased():
    assert parse("/STOP") == ("slash", "stop", [])


def test_multiple_args():
    assert parse("/x a b c") == ("slash", "x", ["a", "b", "c"])


def test_leading_whitespace_slash_command():
    assert parse("  /model gpt-4") == ("slash", "model", ["gpt-4"])


def test_trailing_whitespace_slash_command():
    assert parse("/model gpt-4  ") == ("slash", "model", ["gpt-4"])
