import logging

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from core.config import DATABASE_URL

logger = logging.getLogger("pipelyt.database")

# pool_pre_ping=True       → emit a lightweight "SELECT 1" before checking out a
#                             pooled connection, and silently reconnect if the
#                             server (AWS RDS) closed it while idle. Without
#                             this, the Nexus sequencer's long Gemini waits
#                             let RDS drop the connection, then the next ORM
#                             query in the same tick fails with
#                             PendingRollbackError and cascades through every
#                             remaining row in the batch.
# pool_recycle=300         → proactively rotate connections older than 5 min.
#                             RDS / NAT idle timeouts vary; 5 min is safely
#                             below the most aggressive defaults.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    # Bigger pool than the default 5 + 10 overflow. Local dev runs
    # multiple ticks in parallel (foreground HTTP + the background
    # sequencer loop), and AWS RDS can momentarily reject a checkout
    # under burst. Headroom prevents the cascading "timeout expired"
    # banner when several requests fire at once.
    pool_size=10,
    max_overflow=20,
    pool_timeout=15,
    connect_args={
        # connect_timeout — caps the TCP handshake at 10s so a flaky
        # network surfaces fast instead of hanging the browser for 30s+.
        "connect_timeout": 10,
        # TCP keepalives — tells libpq + the OS to ping the RDS server
        # every N seconds of idle. AWS RDS / corporate firewalls / home
        # NATs silently drop long-idle TCP sockets, which then surface
        # as the "timeout expired" you're seeing in the banner. With
        # keepalives, the OS detects+replaces the dead socket before
        # the app tries to use it.
        "keepalives": 1,
        "keepalives_idle": 30,    # start probing after 30s idle
        "keepalives_interval": 10, # probe every 10s after that
        "keepalives_count": 5,    # 5 unanswered probes = dead, recycle
    },
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def init_db():
    from models import Base
    # Only create PIPELYT tables here. nexus_* tables are owned by
    # apps/backend/nexus/migrations/*.py, which run after this and use
    # `CREATE TABLE/INDEX IF NOT EXISTS` so they are safely idempotent.
    # SQLAlchemy's create_all does not emit IF NOT EXISTS for indexes, so
    # letting it touch nexus_* would crash on the second startup.
    pipelyt_tables = [
        t for t in Base.metadata.sorted_tables
        if not t.name.startswith("nexus_")
    ]
    Base.metadata.create_all(bind=engine, tables=pipelyt_tables)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        # 2026-06-02 — defensive close. `pool_pre_ping=True` checks
        # liveness at CHECKOUT, but a connection can still die WHILE
        # the request holds it (RDS micro-restart, long Gemini wait,
        # idle TCP socket dropped by the firewall mid-request). When
        # the session is autocommit=False, db.close() issues a final
        # rollback against the now-dead socket and raises
        # OperationalError, which propagates as a 500 to the user
        # AFTER the request was already served. Catch + invalidate
        # the underlying connection instead so the bad socket is
        # forced out of the pool and the request finishes cleanly.
        try:
            db.close()
        except OperationalError as exc:
            logger.warning(
                "get_db: connection died before close — invalidating "
                "pooled socket. err=%s", exc,
            )
            try:
                bind = db.get_bind()
                if hasattr(bind, "dispose"):
                    # Force the engine to drop ALL idle connections; the
                    # next request will reopen against a healthy socket.
                    bind.dispose()
            except Exception:  # noqa: BLE001
                pass
        except SQLAlchemyError as exc:
            # Any other SQLAlchemy-level error on close — log + swallow
            # so the request response isn't replaced by a 500. The
            # session is being torn down anyway.
            logger.warning("get_db: db.close() raised %s", exc)
