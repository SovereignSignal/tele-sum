"""Durable rolling-hour request reservations shared by every TeleSum worker."""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.database import get_engine


class AIRequestDeferred(RuntimeError):
    """No provider request was made; preserve the message's retry budget."""


class AIRequestLimiter:
    def __init__(self, max_per_hour: int):
        self.max = max_per_hour

    @staticmethod
    def _engine(db: Session | None):
        return db.get_bind().engine if db is not None else get_engine()

    @staticmethod
    def _clock(dialect: str) -> tuple[str, str]:
        if dialect == "postgresql":
            return "clock_timestamp()", "clock_timestamp() - INTERVAL '1 hour'"
        if dialect == "sqlite":
            return "datetime('now')", "datetime('now', '-1 hour')"
        raise AIRequestDeferred("AI request budget requires PostgreSQL or SQLite")

    def remaining(self, db: Session | None = None) -> int:
        try:
            engine = self._engine(db)
            _, cutoff = self._clock(engine.dialect.name)
            with engine.connect() as conn:
                used = conn.execute(text(
                    f"SELECT count(*) FROM ai_request_reservations WHERE reserved_at > {cutoff}"
                )).scalar_one()
            return max(0, self.max - used)
        except (SQLAlchemyError, ValueError) as exc:
            raise AIRequestDeferred("AI request budget unavailable; work remains queued") from exc

    def can_request(self, db: Session | None = None) -> bool:
        # Advisory only. reserve() makes the authoritative atomic decision.
        return self.remaining(db) > 0

    def reserve(self, db: Session | None = None) -> None:
        """Commit a slot BEFORE inference, independently of the message transaction.

        Failed requests and worker crashes still consume the slot. Reservations
        expire after one hour; restarts and message rollbacks cannot reset them.
        The lock is held only during accounting, never during a provider call.
        """
        try:
            engine = self._engine(db)
            dialect = engine.dialect.name
            now, cutoff = self._clock(dialect)
            with engine.connect() as conn:
                if dialect == "postgresql":
                    # A waiter must see reservations committed by the previous holder.
                    conn = conn.execution_options(isolation_level="READ COMMITTED")
                    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                    conn.execute(text("SET LOCAL statement_timeout = '5s'"))
                    conn.execute(text("SELECT pg_advisory_xact_lock(1414745345)"))
                else:
                    conn.execute(text("BEGIN IMMEDIATE"))
                conn.execute(text(
                    f"DELETE FROM ai_request_reservations WHERE reserved_at <= {cutoff}"
                ))
                used = conn.execute(text("SELECT count(*) FROM ai_request_reservations")).scalar_one()
                if used >= self.max:
                    raise AIRequestDeferred("Hourly AI request limit reached; work remains queued")
                conn.execute(text(
                    f"INSERT INTO ai_request_reservations (reserved_at) VALUES ({now})"
                ))
                conn.commit()
        except (SQLAlchemyError, ValueError) as exc:
            raise AIRequestDeferred("AI request budget unavailable; work remains queued") from exc
