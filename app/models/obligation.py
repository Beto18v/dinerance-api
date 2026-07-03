import enum
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import relationship

from app.database.base import Base


class ObligationCadence(str, enum.Enum):
    weekly = "weekly"
    biweekly = "biweekly"
    monthly = "monthly"


class ObligationStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    archived = "archived"


class Obligation(Base):
    __tablename__ = "obligations"

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

    category_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=False,
    )

    expected_financial_account_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("financial_accounts.id"),
        nullable=True,
    )

    name = Column(
        String,
        nullable=False,
    )

    source_recurring_candidate_key = Column(
        String(64),
        nullable=True,
    )

    amount = Column(
        Numeric(12, 2),
        nullable=False,
    )

    currency = Column(
        String(3),
        nullable=False,
    )

    cadence = Column(
        Enum(ObligationCadence),
        nullable=False,
    )

    next_due_date = Column(
        Date,
        nullable=False,
    )

    monthly_anchor_day = Column(
        Integer,
        nullable=True,
    )

    monthly_anchor_is_month_end = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    cadence_months = Column(
        Integer,
        nullable=True,
    )

    status = Column(
        Enum(ObligationStatus),
        nullable=False,
        default=ObligationStatus.active,
        server_default=ObligationStatus.active.value,
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        onupdate=func.now(),
    )

    user = relationship("User", back_populates="obligations")
    category = relationship("Category", back_populates="obligations")
    expected_financial_account = relationship(
        "FinancialAccount",
        back_populates="expected_obligations",
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_obligations_amount_positive"),
        CheckConstraint(
            """
            (
                cadence = 'monthly'
                AND (
                    monthly_anchor_day BETWEEN 1 AND 31
                    OR monthly_anchor_is_month_end
                )
            )
            OR (
                cadence IN ('weekly', 'biweekly')
                AND monthly_anchor_day IS NULL
            )
            """,
            name="ck_obligations_monthly_anchor",
        ),
        CheckConstraint(
            """
            cadence_months IS NULL
            OR cadence_months >= 1
            """,
            name="ck_obligations_cadence_months_positive",
        ),
        Index(
            "idx_obligations_user_status_due",
            "user_id",
            "status",
            "next_due_date",
        ),
        Index(
            "idx_obligations_user_category",
            "user_id",
            "category_id",
        ),
        Index(
            "idx_obligations_user_recurring_candidate_key",
            "user_id",
            "source_recurring_candidate_key",
        ),
        Index(
            "idx_obligations_user_expected_account",
            "user_id",
            "expected_financial_account_id",
        ),
    )
