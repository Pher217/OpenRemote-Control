from apps.observe.formatting import _esc, format_turn, md_to_telegram_html


def test_esc_escapes_in_order():
    """GIVEN ampersand and angle brackets WHEN escaped THEN entities in order."""
    assert _esc("a<b & c>") == "a&lt;b &amp; c&gt;"


def test_fenced_code_block_escaped_no_markdown():
    """GIVEN a fenced code block WHEN converted THEN wrapped in <pre>, escaped."""
    out = md_to_telegram_html("```py\nx<1 & y\n```")
    assert "<pre>" in out
    assert "x&lt;1 &amp; y" in out
    assert "<b>" not in out


def test_inline_code_escaped():
    """GIVEN an inline code span WHEN converted THEN wrapped in <code>, escaped."""
    assert md_to_telegram_html("use `x<y`") == "use <code>x&lt;y</code>"


def test_bold():
    """GIVEN markdown bold WHEN converted THEN <b> tags."""
    assert md_to_telegram_html("**hi**") == "<b>hi</b>"


def test_markdown_link():
    """GIVEN a markdown link WHEN converted THEN an <a href> with the label."""
    out = md_to_telegram_html("[PR #5](https://github.com/o/r/pull/5)")
    assert out == '<a href="https://github.com/o/r/pull/5">PR #5</a>'


def test_bare_url_wrapped():
    """GIVEN a bare URL WHEN converted THEN it is wrapped in an <a>."""
    out = md_to_telegram_html("see https://github.com/o/r/commit/abc")
    assert (
        '<a href="https://github.com/o/r/commit/abc">https://github.com/o/r/commit/abc</a>'
        in out
    )


def test_script_tag_escaped():
    """GIVEN a script tag in plain text WHEN converted THEN no raw tag survives."""
    out = md_to_telegram_html("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_format_turn_user_label_and_emoji():
    """GIVEN a user turn WHEN formatted THEN uses user_label, 🧑, and bold header."""
    out = format_turn(
        {"role": "user", "text": "hi"}, user_label="Phil", assistant_label="Claude"
    )
    assert out.startswith("<b>")
    assert "🧑 Phil" in out


def test_format_turn_assistant_label_and_emoji():
    """GIVEN an assistant turn WHEN formatted THEN uses assistant_label and 🤖."""
    out = format_turn(
        {"role": "assistant", "text": "yo"}, user_label="Phil", assistant_label="Claude"
    )
    assert "🤖 Claude" in out


def test_format_turn_truncates_long_body():
    """GIVEN a body over 4000 chars WHEN formatted THEN output stays under 4096."""
    out = format_turn(
        {"role": "assistant", "text": "x" * 5000},
        user_label="Phil",
        assistant_label="Claude",
    )
    assert len(out) < 4096
