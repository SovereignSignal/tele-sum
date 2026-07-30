"""The bot token must never reach the platform log.

httpx logs every request line at INFO. The Telegram Bot API carries the bot
token in the URL path, so INFO-level httpx logging emitted

    HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getUpdates "200 OK"

into Railway's log on every poll cycle. Confirmed live 2026-07-27. These tests
pin the fix so nobody re-enables it by raising the root level.
"""

import logging


def test_httpx_request_lines_are_suppressed():
    import src.config  # noqa: F401  -- import configures logging as a side effect

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING, (
        "httpx must not log at INFO: its request lines contain the bot token"
    )
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_a_token_bearing_request_line_would_not_emit(caplog):
    """An INFO record on the httpx logger must be dropped, not recorded."""
    import src.config  # noqa: F401

    httpx_logger = logging.getLogger("httpx")
    with caplog.at_level(logging.DEBUG, logger="root"):
        httpx_logger.info(
            'HTTP Request: POST https://api.telegram.org/bot123456:FAKE-TOKEN/getUpdates "HTTP/1.1 200 OK"'
        )

    assert not any("FAKE-TOKEN" in r.getMessage() for r in caplog.records), (
        "a token-bearing httpx INFO line was still emitted"
    )


def test_real_failures_still_surface():
    """Silencing INFO must not silence errors -- WARNING and above still log."""
    import src.config  # noqa: F401

    assert logging.getLogger("httpx").isEnabledFor(logging.WARNING)
    assert logging.getLogger("httpx").isEnabledFor(logging.ERROR)
