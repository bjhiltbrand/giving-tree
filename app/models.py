from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_token() -> str:
    return secrets.token_urlsafe(24)


# Claim lifecycle:
#   pending   -> held, waiting for the claimant to click the email confirmation link
#   claimed   -> active claim; the claimant is shopping
#   delivered -> claimant says they dropped it off; waiting for an admin to confirm receipt
#   fulfilled -> admin confirmed the gift was received
#   released  -> cancelled (by claimant, admin, or an expired confirmation); item is available again
CLAIM_STATUSES = {
    "pending": "Awaiting email confirmation",
    "claimed": "Claimed",
    "delivered": "Dropped off",
    "fulfilled": "Received",
    "released": "Released",
}
ACTIVE_CLAIM_STATUSES = ("pending", "claimed", "delivered", "fulfilled")


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SiteSetting(Base):
    __tablename__ = "site_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Family(Base):
    __tablename__ = "families"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Public, privacy-preserving name, e.g. "Family A" or "The Evergreen Family".
    alias: Mapped[str] = mapped_column(String(120))
    # Shown publicly, e.g. "Mom and three kids (ages 4, 8, 12)".
    description: Mapped[str] = mapped_column(Text, default="")
    # Only visible to admins (real names, contact info, delivery notes...).
    private_notes: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    items: Mapped[list[Item]] = relationship(
        back_populates="family",
        cascade="all, delete-orphan",
        order_by="(Item.sort_order, Item.id)",
    )


class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40), default="other")  # a key from categories.py
    recipient: Mapped[str] = mapped_column(String(120), default="")  # e.g. "Girl, age 8"
    size: Mapped[str] = mapped_column(String(80), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    link_url: Mapped[str] = mapped_column(String(500), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    family: Mapped[Family] = relationship(back_populates="items")
    claims: Mapped[list[Claim]] = relationship(back_populates="item", cascade="all, delete-orphan")

    @property
    def claimed_qty(self) -> int:
        return sum(c.quantity for c in self.claims if c.status in ACTIVE_CLAIM_STATUSES)

    @property
    def fulfilled_qty(self) -> int:
        return sum(c.quantity for c in self.claims if c.status == "fulfilled")

    @property
    def remaining(self) -> int:
        return max(0, self.quantity - self.claimed_qty)

    @property
    def state(self) -> str:
        if self.remaining > 0:
            return "partial" if self.claimed_qty else "open"
        if self.fulfilled_qty >= self.quantity:
            return "fulfilled"
        return "claimed"


class Claimant(Base):
    """A visitor, identified only by email. No password; access is via a private link."""

    __tablename__ = "claimants"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=new_token)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    claims: Mapped[list[Claim]] = relationship(back_populates="claimant", order_by="Claim.created_at.desc()")


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    claimant_id: Mapped[int] = mapped_column(ForeignKey("claimants.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="claimed", index=True)
    note: Mapped[str] = mapped_column(Text, default="")  # note from the claimant
    admin_note: Mapped[str] = mapped_column(Text, default="")
    confirm_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=new_token)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    release_reason: Mapped[str] = mapped_column(String(200), default="")

    item: Mapped[Item] = relationship(back_populates="claims")
    claimant: Mapped[Claimant] = relationship(back_populates="claims")
    reminders: Mapped[list[ReminderLog]] = relationship(back_populates="claim", cascade="all, delete-orphan")

    @property
    def status_label(self) -> str:
        return CLAIM_STATUSES.get(self.status, self.status)


class ReminderLog(Base):
    __tablename__ = "reminder_log"
    __table_args__ = (UniqueConstraint("claim_id", "kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    sent: Mapped[bool] = mapped_column(Boolean, default=True)  # False = skipped (not applicable)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    claim: Mapped[Claim] = relationship(back_populates="reminders")


class EmailTemplate(Base):
    """Admin-customized wording for a giver-facing email (see email_copy.DEFAULTS)."""

    __tablename__ = "email_templates"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject: Mapped[str] = mapped_column(String(300), default="")
    intro: Mapped[str] = mapped_column(Text, default="")
    outro: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EmailLog(Base):
    __tablename__ = "email_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    to_addr: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | sent | logged | failed
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
