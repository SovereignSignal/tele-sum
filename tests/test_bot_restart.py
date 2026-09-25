from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.services import telegram_bot


async def test_restart_keeps_pending_telegram_updates(monkeypatch):
    app = SimpleNamespace(initialize=AsyncMock(), start=AsyncMock(),
                          add_handler=MagicMock(),
                          updater=SimpleNamespace(start_polling=AsyncMock()))
    builder = MagicMock()
    builder.token.return_value.build.return_value = app
    monkeypatch.setattr(telegram_bot.Application, "builder", lambda: builder)
    bot = telegram_bot.TelegramBotService("123456:fake-token", bot_id=1)
    await bot.start_polling()
    assert app.updater.start_polling.call_args.kwargs["drop_pending_updates"] is False
    assert bot.is_running
