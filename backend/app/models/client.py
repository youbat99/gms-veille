import uuid
import enum
from sqlalchemy import String, Boolean, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin, UUIDMixin


class AccountRole(str, enum.Enum):
    super_admin  = "super_admin"   # GMS — accès total
    admin        = "admin"         # Admin GMS — gère ses clients + crée valideurs
    validator    = "validator"     # Valideur GMS — valide articles de son client
    client_admin = "client_admin"  # Admin côté client — gère son org + crée client_user
    client_user  = "client_user"   # Utilisateur client — lecture seule selon droits assignés


class Client(Base, UUIDMixin, TimestampMixin):
    """Organisation cliente (ministère, entreprise, agence...)"""
    __tablename__ = "clients"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    subscription_plan: Mapped[str] = mapped_column(String(50), default="starter")

    accounts: Mapped[list["Account"]] = relationship(
        back_populates="client", cascade="all, delete-orphan",
        foreign_keys="Account.client_id",
    )
    revues: Mapped[list["Revue"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    media_sources: Mapped[list["ClientMediaSource"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class Account(Base, UUIDMixin, TimestampMixin):
    """Compte utilisateur — super_admin sans client, admin/validator liés à un client."""
    __tablename__ = "accounts"

    # Null pour super_admin, requis pour admin et validator
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[AccountRole] = mapped_column(Enum(AccountRole, name="accountrole"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Qui a créé ce compte (traçabilité)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True
    )

    client: Mapped["Client | None"] = relationship(
        back_populates="accounts", foreign_keys=[client_id]
    )
    creator: Mapped["Account | None"] = relationship(
        "Account", remote_side="Account.id", foreign_keys=[created_by]
    )
    revue_accesses: Mapped[list["UserAccount"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class UserAccount(Base, TimestampMixin):
    """Accès d'un compte à une revue spécifique (utilisé pour client_admin / client_user)"""
    __tablename__ = "user_accounts"

    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), primary_key=True)
    revue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("revues.id"), primary_key=True)
    can_export: Mapped[bool] = mapped_column(Boolean, default=False)
    can_view_dashboard: Mapped[bool] = mapped_column(Boolean, default=True)

    account: Mapped["Account"] = relationship(back_populates="revue_accesses")
    revue: Mapped["Revue"] = relationship(back_populates="user_accesses")


class ClientMediaSource(Base, TimestampMixin):
    """Sources media accessibles pour un client (service vendu)"""
    __tablename__ = "client_media_sources"

    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("media_sources.id", ondelete="CASCADE"), primary_key=True)

    client: Mapped["Client"] = relationship(back_populates="media_sources")
