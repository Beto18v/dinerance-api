import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import relationship

from app.database.base import Base


class FinancialAccount(Base):
    __tablename__ = "financial_accounts"

    id = Column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    name = Column(
        String,
        nullable=False,
    )

    currency = Column(
        String(3),
        nullable=True,
    )

    is_default = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        onupdate=func.now(),
    )

    # Timestamp for soft deletion, nullable to indicate active records
    deleted_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    user = relationship("User", back_populates="financial_accounts")

    transactions = relationship(
        "Transaction",
        back_populates="financial_account",
    )

    expected_obligations = relationship(
        "Obligation",
        back_populates="expected_financial_account",
    )

    __table_args__ = (
        Index("idx_financial_accounts_user", "user_id"),
        Index(
            "uq_financial_accounts_default_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
            sqlite_where=text("is_default = 1"),
        ),
    )
