"""Tests for the TipTap-HTML -> ReportLab-markup converter.

The frontend's RichTextEditor sends its fields (Service description, the
printed Note) as HTML. This converter is the only thing standing between
that HTML and a ReportLab ``Paragraph``, so two things matter and are
asserted separately:

  1. The right marks survive -- bold/italic/underline/strike, lists,
     headings, and (new) colour, highlight and font size.
  2. Whatever comes in, the output is markup ReportLab will actually
     accept. Malformed markup doesn't degrade gracefully in ReportLab; it
     raises, which would take down the whole cost-agreement PDF. So every
     case here is also fed through a real Paragraph.wrap().
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from costagreements.components import rich_text_to_paragraph_markup as convert

STYLE = ParagraphStyle("t", fontName="Helvetica", fontSize=9, leading=12)


def renders(markup: str) -> bool:
    """True if ReportLab accepts the markup (blank is vacuously fine)."""
    Paragraph(markup or " ", STYLE).wrap(400, 400)
    return True


# --------------------------------------------------------------- marks survive
def test_basic_inline_marks_map_to_reportlab_tags():
    out = convert("<p>a <strong>b</strong> <em>c</em> <u>d</u> <s>e</s></p>")
    assert out == "a <b>b</b> <i>c</i> <u>d</u> <strike>e</strike>"
    assert renders(out)


def test_text_colour_becomes_a_font_color():
    out = convert('<p><span style="color:#c0392b">red</span></p>')
    assert out == '<font color="#c0392b">red</font>'
    assert renders(out)


def test_highlight_becomes_a_font_backcolor():
    out = convert('<p><mark data-color="#fef08a">hl</mark></p>')
    assert out == '<font backColor="#fef08a">hl</font>'
    assert renders(out)


def test_highlight_from_css_background_colour():
    out = convert('<p><mark style="background-color:#bbf7d0">hl</mark></p>')
    assert 'backColor="#bbf7d0"' in out
    assert renders(out)


def test_highlight_with_no_usable_colour_still_highlights():
    # A highlight the editor wrote without a colour still means "highlighted".
    out = convert("<p><mark>hl</mark></p>")
    assert "backColor=" in out
    assert renders(out)


def test_font_size_becomes_a_font_size_and_is_clamped():
    assert convert('<p><span style="font-size:18px">x</span></p>') == '<font size="18">x</font>'
    # Absurd sizes would blow the frame apart, so they're clamped, not passed on.
    assert convert('<p><span style="font-size:99px">x</span></p>') == '<font size="24">x</font>'
    assert convert('<p><span style="font-size:2px">x</span></p>') == '<font size="6">x</font>'


def test_headings_become_sized_bold_runs():
    out = convert("<h1>Big</h1><h2>Mid</h2><h3>Small</h3>")
    assert '<font size="13"><b>Big</b></font>' in out
    assert '<font size="11"><b>Mid</b></font>' in out
    assert '<font size="10"><b>Small</b></font>' in out
    assert renders(out)


def test_bullet_and_numbered_lists_become_prefixed_lines():
    assert convert("<ul><li><p>one</p></li><li><p>two</p></li></ul>") == "• one<br/>• two"
    assert convert("<ol><li><p>one</p></li><li><p>two</p></li></ol>") == "1. one<br/>2. two"


def test_nested_lists_indent():
    out = convert("<ul><li><p>a</p><ul><li><p>b</p></li></ul></li></ul>")
    assert "&nbsp;&nbsp;&nbsp;&nbsp;• b" in out
    assert renders(out)


# ------------------------------------------------- nesting closes in the right order
@pytest.mark.parametrize("html", [
    '<p><span style="color:#c0392b"><strong>bold red</strong></span></p>',
    '<p><strong><span style="color:#c0392b">red bold</span></strong></p>',
    '<p><mark data-color="#bbf7d0"><span style="color:#2980b9">both</span></mark></p>',
    '<p><em><u><strong><span style="color:#111111">all four</span></strong></u></em></p>',
])
def test_overlapping_marks_produce_well_formed_markup(html):
    # Closing tags used to be emitted positionally, so bold inside a colour
    # span produced "<font><b>x</font></b>" -- which ReportLab rejects.
    out = convert(html)
    assert renders(out)


def test_colour_inside_bold_keeps_both():
    out = convert('<p><strong>plain <span style="color:#c0392b">red</span></strong></p>')
    assert "<b>" in out and 'color="#c0392b"' in out
    assert renders(out)


# --------------------------------------------------------------- defensive cases
def test_unparseable_colours_are_dropped_not_forwarded():
    # ReportLab raises on a colour it can't parse; dropping it loses styling,
    # forwarding it would lose the whole document.
    for bad in ("notacolour", "rgb(1,2,3)", "var(--x)", "red"):
        out = convert(f'<p><span style="color:{bad}">text</span></p>')
        assert out == "text"
        assert renders(out)


def test_short_hex_colour_is_accepted():
    assert convert('<p><span style="color:#abc">x</span></p>') == '<font color="#abc">x</font>'


@pytest.mark.parametrize("html", [
    "<p><strong>never closed",
    "<p><strong>a<em>b</strong>c</em></p>",
    "<p>unclosed <span style='color:#111111'>span",
    "<ul><li><p>dangling",
])
def test_malformed_html_still_renders(html):
    assert renders(convert(html))


def test_unknown_tags_are_unwrapped_keeping_their_text():
    assert convert("<p>keep <div>this</div></p>").startswith("keep ")
    assert "this" in convert("<p>keep <div>this</div></p>")
    assert "<div>" not in convert("<p>keep <div>this</div></p>")


def test_typed_angle_brackets_and_ampersands_are_escaped():
    out = convert("<p>1 < 2 &amp; 3 > 2</p>")
    assert "&lt;" in out and "&amp;" in out
    assert renders(out)


def test_legacy_plain_text_falls_back_to_line_breaks():
    # Drafts saved before the rich-text editor existed are newline-separated.
    assert convert("line one\nline two") == "line one<br/>line two"


def test_empty_values_produce_empty_markup():
    assert convert("") == ""
    assert convert("   ") == ""
    assert convert("<p></p>") == ""


def test_alignment_is_dropped_deliberately():
    # ParagraphStyle-only in ReportLab; documented as unsupported rather
    # than half-implemented. Asserted so a future change is a conscious one.
    out = convert('<p style="text-align:center">centred</p>')
    assert out == "centred"
    assert "align" not in out


# ------------------------------------------------------ bulleted_html (works table)
from costagreements.components import bulleted_html  # noqa: E402


def test_plain_boilerplate_bullets_are_still_escaped():
    # Most callers pass hardcoded English; that behaviour must not change.
    out = bulleted_html(["Process your application", "Follow up & finalise"])
    assert out == "•  Process your application<br/>•  Follow up &amp; finalise"
    assert renders(out)


def test_editor_authored_bullets_keep_their_formatting():
    # service_bullets is one blob of TipTap HTML with no newlines in it;
    # escaping it printed "<ul><li><p>" into the works table as visible text.
    out = bulleted_html([
        "<ul><li><p>Prepare and <strong>lodge</strong></p></li>"
        '<li><p><span style="color:#c0392b">Urgent</span> liaison</p></li></ul>'
    ])
    assert "<b>lodge</b>" in out
    assert 'color="#c0392b"' in out
    assert "&lt;" not in out
    assert renders(out)


def test_html_list_is_not_given_a_second_bullet():
    out = bulleted_html(["<ul><li><p>one</p></li></ul>"])
    assert out.count("•") == 1
    assert renders(out)


def test_non_list_html_item_still_gets_a_bullet():
    out = bulleted_html(["<p>single <em>item</em></p>"])
    assert out.startswith("•")
    assert "<i>item</i>" in out


def test_empty_bullet_list_is_empty():
    assert bulleted_html([]) == ""
    assert bulleted_html(["<p></p>"]) == ""
