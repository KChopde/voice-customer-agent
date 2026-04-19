from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "support.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, default="Guest")
    phone = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    orders = relationship("Order", back_populates="user")
    complaints = relationship("Complaint", back_populates="user")
    feedbacks = relationship("Feedback", back_populates="user")


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    product = Column(String(200), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    address = Column(String(500), nullable=True)
    status = Column(String(40), nullable=False, default="confirmed")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="orders")


class Complaint(Base):
    __tablename__ = "complaints"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    issue = Column(Text, nullable=False)
    status = Column(String(40), nullable=False, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="complaints")


class Feedback(Base):
    __tablename__ = "feedback"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rating = Column(Integer, nullable=True)  # 1-5
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="feedbacks")


class Inquiry(Base):
    """Catch-all bucket for AI-classified task types that don't have a
    dedicated handler yet. Acts as a backlog of "things customers asked
    that we should add to task_types.json".
    """

    __tablename__ = "inquiries"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    task_type = Column(String(60), nullable=False)
    summary = Column(Text, nullable=False)
    details = Column(Text, nullable=True)  # JSON blob of extracted fields
    status = Column(String(40), nullable=False, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)


class Grocery(Base):
    """Catalog of items the agent can sell. Seeded by `python seed.py`."""

    __tablename__ = "groceries"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, index=True)
    category = Column(String(60), nullable=False, index=True)
    unit = Column(String(20), nullable=False)
    price = Column(Float, nullable=False)
    stock = Column(Integer, nullable=False, default=0)

    aliases = relationship(
        "GroceryAlias", back_populates="grocery", cascade="all, delete-orphan"
    )


class GroceryAlias(Base):
    """Alternate names ('aloo' for 'Potato', etc.) used for fuzzy matching."""

    __tablename__ = "grocery_aliases"
    id = Column(Integer, primary_key=True)
    grocery_id = Column(
        Integer, ForeignKey("groceries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias = Column(String(120), nullable=False, index=True)

    grocery = relationship("Grocery", back_populates="aliases")


class GrocerySubstitute(Base):
    """Suggested replacements when a grocery is out of stock."""

    __tablename__ = "grocery_substitutes"
    id = Column(Integer, primary_key=True)
    grocery_id = Column(
        Integer, ForeignKey("groceries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    substitute_id = Column(
        Integer, ForeignKey("groceries.id", ondelete="CASCADE"), nullable=False
    )


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # 'user' or 'assistant'
    message = Column(Text, nullable=False)
    task_type = Column(String(60), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


def _migrate_legacy_columns() -> None:
    """Rename the legacy 'intent' column to 'task_type' if upgrading
    an older dev database. Safe no-op on fresh installs.
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "conversations" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("conversations")}
    if "intent" in cols and "task_type" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE conversations RENAME COLUMN intent TO task_type"))


def init_db() -> None:
    """Create all tables if they don't exist (and migrate legacy schemas)."""
    _migrate_legacy_columns()
    Base.metadata.create_all(bind=engine)


def get_session():
    """FastAPI dependency that yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
