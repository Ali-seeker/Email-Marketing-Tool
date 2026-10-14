"""Database setup using SQLAlchemy 2.x and SQLite in WAL mode.

Configures connection engine, session factory, WAL mode pragma listeners,
and declarative base class.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, types
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""

    pass


class UTCDateTime(types.TypeDecorator):
    """Timezone-aware UTC DateTime type for robust cross-database serialization."""

    impl = types.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None:
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None:
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        return value


def _ensure_sqlite_directory(url: str) -> None:
    """Ensure parent directory exists for SQLite database files."""
    if url.startswith("sqlite:///"):
        db_path_str = url.replace("sqlite:///", "")
        db_path = Path(db_path_str)
        if db_path.parent and not db_path.parent.exists():
            db_path.parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
_ensure_sqlite_directory(settings.DATABASE_URL)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    """Enable SQLite WAL mode and foreign key constraints for concurrent read/write."""
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:
        # Ignore if running against non-SQLite databases in other environments
        pass


SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def init_db(target_engine: Engine | None = None) -> None:
    """Create all database tables using metadata.create_all and apply simple migrations."""
    from sqlalchemy import text

    import app.models  # noqa: F401

    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)

    # Simple column migrations for existing SQLite databases
    with eng.connect() as conn:
        try:
            conn.execute(
                text("ALTER TABLE suppressions ADD COLUMN is_domain BOOLEAN DEFAULT 0 NOT NULL")
            )
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("ALTER TABLE leads ADD COLUMN risk_level VARCHAR(50)"))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("ALTER TABLE leads ADD COLUMN validation_reason VARCHAR(255)"))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("ALTER TABLE leads ADD COLUMN evidence_quote VARCHAR(500)"))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(
                text("ALTER TABLE email_messages ADD COLUMN subject_variant_index INTEGER")
            )
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("ALTER TABLE email_messages ADD COLUMN body_variant_index INTEGER"))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("ALTER TABLE email_messages ADD COLUMN sending_started_at DATETIME"))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(
                text("ALTER TABLE email_messages ADD COLUMN retry_count INTEGER DEFAULT 0 NOT NULL")
            )
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("ALTER TABLE email_messages ADD COLUMN earliest_send_at DATETIME"))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("ALTER TABLE email_messages ADD COLUMN offset_days INTEGER"))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("ALTER TABLE leads ADD COLUMN mailbox_id INTEGER REFERENCES mailboxes(id)"))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("ALTER TABLE email_messages ADD COLUMN mailbox_id INTEGER REFERENCES mailboxes(id)"))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("ALTER TABLE email_messages ADD COLUMN from_email VARCHAR(320)"))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("ALTER TABLE replies ADD COLUMN mailbox_id INTEGER REFERENCES mailboxes(id)"))
            conn.commit()
        except Exception:
            pass

    # Auto-seed default mailbox from .env if mailboxes table is empty
    with Session(eng) as s:
        from sqlalchemy import select
        from app.models.mailbox import Mailbox

        has_any = s.scalar(select(Mailbox).limit(1))
        if not has_any:
            cfg = get_settings()
            if cfg.SMTP_USERNAME and cfg.SMTP_USERNAME != "user@example.com":
                pwd = cfg.SMTP_PASSWORD.get_secret_value() if cfg.SMTP_PASSWORD else ""
                imap_pwd = (
                    cfg.IMAP_APP_PASSWORD.get_secret_value()
                    if cfg.IMAP_APP_PASSWORD
                    else pwd
                )
                default_box = Mailbox(
                    email=cfg.SMTP_FROM_EMAIL or cfg.SMTP_USERNAME,
                    display_name=cfg.SMTP_FROM_NAME or "ColdReach Operator",
                    smtp_host=cfg.SMTP_HOST,
                    smtp_port=cfg.SMTP_PORT,
                    smtp_username=cfg.SMTP_USERNAME,
                    smtp_password=pwd,
                    smtp_use_ssl=(cfg.SMTP_PORT == 465),
                    imap_host=cfg.IMAP_HOST,
                    imap_port=cfg.IMAP_PORT,
                    imap_username=cfg.IMAP_USER or cfg.SMTP_USERNAME,
                    imap_password=imap_pwd,
                    imap_use_ssl=cfg.IMAP_USE_SSL,
                    daily_send_cap=cfg.MAX_EMAILS_PER_DAY_PER_MAILBOX or 30,
                    is_active=True,
                )
                s.add(default_box)
                s.commit()


def get_db() -> Generator[Session, None, None]:
    """Dependency provider yielding a scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
