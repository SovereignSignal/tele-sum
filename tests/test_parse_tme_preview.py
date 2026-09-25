from scripts.parse_tme_preview import parse_preview

# Minimal fixture mirroring t.me/s/<channel> markup: a service message (skipped),
# a real content post (kept), and a textless post (skipped).
HTML = """
<div class="tgme_widget_message service_message js-widget_message" data-post="interestingrepos/3">
  <div class="tgme_widget_message_text js-message_text">Channel photo updated</div>
</div>
<div class="tgme_widget_message js-widget_message" data-post="interestingrepos/8">
  <div class="tgme_widget_message_text js-message_text">Open Design Platform <a href="https://github.com/nexu-io/open-design">repo</a></div>
  <div class="tgme_widget_message_info"><time datetime="2026-05-27T10:00:00+00:00">10:00</time></div>
</div>
<div class="tgme_widget_message js-widget_message" data-post="interestingrepos/9">
  <div class="tgme_widget_message_text js-message_text">   </div>
</div>
"""


def test_parse_skips_service_messages_and_keeps_real_posts():
    rows = parse_preview(HTML)
    ids = [r["telegram_id"] for r in rows]
    assert ids == [8]                                   # service (3) + textless (9) dropped


def test_parse_extracts_fields():
    row = parse_preview(HTML)[0]
    assert row["telegram_id"] == 8
    assert "Open Design Platform" in row["text"]
    assert row["url"] == "https://github.com/nexu-io/open-design"
    assert row["entity_links"] == [{"text": "repo", "url": "https://github.com/nexu-io/open-design"}]
    assert row["posted_at"] == "2026-05-27T10:00:00+00:00"


def test_parse_ignores_telegram_hashtag_search_links():
    html = HTML.replace('Open Design Platform', '<a href="?q=%23Repo">#Repo</a> Open Design Platform')
    row = parse_preview(html)[0]
    assert row["url"] == "https://github.com/nexu-io/open-design"
    assert len(row["entity_links"]) == 1
