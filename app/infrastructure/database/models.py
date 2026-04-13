"""
database/models.py — Définition de toutes les tables de la base de données.

Chaque classe = une table PostgreSQL.
Chaque attribut Mapped[type] = une colonne.
Les relationship() définissent comment naviguer entre les tables en Python.

Import important : ce fichier doit être importé dans alembic/env.py
pour qu'Alembic détecte les tables et génère les migrations.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin


# ─────────────────────────────────────────────────────────────────────────────
# MEMBER — Les membres de l'association
# ─────────────────────────────────────────────────────────────────────────────
class Member(Base, UUIDMixin, TimestampMixin):
    """
    Table centrale : tout pointe vers Member.
    Un membre peut être invité (status=pending) avant d'avoir créé son compte.
    Le champ invitation_token est un lien unique envoyé par l'admin.
    """
    __tablename__ = "members"

    full_name: Mapped[str] = mapped_column(String(150), nullable=False)

    # index=True : accélère les recherches par téléphone (connexion, vérification doublon)
    phone_number: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    email: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    # On ne stocke JAMAIS le mot de passe en clair, seulement son hash bcrypt
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Rôle : admin | treasurer | manager | member
    role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)

    # Statut : pending (invité) | active | inactive | suspended
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)

    # Token unique envoyé par l'admin pour créer son compte
    invitation_token: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    invited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Rempli quand le membre finalise son inscription
    joined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Photo de profil (AWS S3) ───────────────────────────────────────────────
    # Stocke l'URL publique S3 de la photo uploadée.
    # Null si aucune photo n'a encore été définie.
    profile_picture_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    # ── Relations ─────────────────────────────────────────────────────────────
    contributions: Mapped[list["Contribution"]] = relationship(
    "Contribution",
    back_populates="member",
    foreign_keys="[Contribution.member_id]",
    cascade="all, delete-orphan",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="member", cascade="all, delete-orphan"
    )
    transactions_performed: Mapped[list["Transaction"]] = relationship(
        back_populates="performed_by_member",
        foreign_keys="Transaction.performed_by",
    )
    transactions_as_subject: Mapped[list["Transaction"]] = relationship(
        back_populates="subject_member",
        foreign_keys="Transaction.member_id",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="member")

    def __repr__(self) -> str:
        return f"<Member {self.full_name} ({self.phone_number}) role={self.role}>"


# ─────────────────────────────────────────────────────────────────────────────
# ASSOCIATION SETTINGS — Configuration globale (singleton)
# ─────────────────────────────────────────────────────────────────────────────
class AssociationSettings(Base, UUIDMixin):
    """
    Table singleton : UNE SEULE LIGNE contient la config de l'association.
    C'est ici qu'on bascule entre mode de cotisation "free" et "fixed".
    """
    __tablename__ = "association_settings"

    contribution_mode: Mapped[str] = mapped_column(
        String(10), default="free", nullable=False
    )
    fixed_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="XOF", nullable=False)

    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    updater: Mapped["Member | None"] = relationship("Member", foreign_keys=[updated_by])

    def __repr__(self) -> str:
        return f"<AssociationSettings mode={self.contribution_mode} fixed={self.fixed_amount}>"


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT
# ─────────────────────────────────────────────────────────────────────────────
class Project(Base, UUIDMixin, TimestampMixin):
    """
    Représente un projet de l'association (construction, achat, événement...).
    budget_spent est mis à jour automatiquement lors de chaque dépense liée.
    """
    __tablename__ = "projects"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    budget_allocated: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    budget_spent: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="RESTRICT"),
        nullable=False,
    )
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    creator: Mapped["Member"] = relationship("Member", foreign_keys=[created_by])
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="project")

    @property
    def budget_remaining(self) -> float | None:
        if self.budget_allocated is None:
            return None
        return float(self.budget_allocated) - float(self.budget_spent)

    def __repr__(self) -> str:
        return f"<Project {self.title} status={self.status}>"


# ─────────────────────────────────────────────────────────────────────────────
# TREASURY BALANCE — Solde de la caisse
# ─────────────────────────────────────────────────────────────────────────────
class TreasuryBalance(Base, UUIDMixin):
    """
    Table singleton : solde actuel de la caisse.
    Le comptable l'initialise une fois pour synchroniser l'existant.
    Ensuite, chaque transaction met à jour 'balance'.
    """
    __tablename__ = "treasury_balance"

    balance: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    initial_balance: Mapped[float] = mapped_column(
        Numeric(14, 2), default=0, nullable=False
    )

    initialized_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
    )
    initialized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    initializer: Mapped["Member | None"] = relationship(
        "Member", foreign_keys=[initialized_by]
    )

    def __repr__(self) -> str:
        return f"<TreasuryBalance {self.balance} XOF>"


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION — Chaque mouvement d'argent
# ─────────────────────────────────────────────────────────────────────────────
class Transaction(Base, UUIDMixin, TimestampMixin):
    """
    Historique immuable de tous les mouvements financiers.
    Types : deposit (cotisation confirmée) | expense (dépense) | adjustment (init)
    """
    __tablename__ = "transactions"

    type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    balance_after: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    performed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30), default="confirmed", nullable=False
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    subject_member: Mapped["Member | None"] = relationship(
        "Member",
        back_populates="transactions_as_subject",
        foreign_keys=[member_id],
    )
    performed_by_member: Mapped["Member"] = relationship(
        "Member",
        back_populates="transactions_performed",
        foreign_keys=[performed_by],
    )
    approver: Mapped["Member | None"] = relationship(
        "Member", foreign_keys=[approved_by]
    )
    project: Mapped["Project | None"] = relationship(back_populates="transactions")
    contribution: Mapped["Contribution | None"] = relationship(
        back_populates="transaction"
    )

    def __repr__(self) -> str:
        return f"<Transaction {self.type} {self.amount} XOF status={self.status}>"


# ─────────────────────────────────────────────────────────────────────────────
# CONTRIBUTION — Cotisation d'un membre pour un mois donné
# ─────────────────────────────────────────────────────────────────────────────
class Contribution(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "contributions"

    __table_args__ = (
        UniqueConstraint(
            "member_id", "contribution_month", "contribution_year",
            name="uq_contribution_member_month_year",
        ),
    )

    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
    )
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )

    contribution_month: Mapped[int] = mapped_column(Integer, nullable=False)  # 1–12
    contribution_year: Mapped[int] = mapped_column(Integer, nullable=False)   # ex: 2026

    # pending | declared | confirmed
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False
    )

    # Montant réellement reçu
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Snapshot du montant attendu (null en mode free, montant fixe en mode fixed)
    expected_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    # Snapshot du mode actif quand la cotisation a été créée
    contribution_mode: Mapped[str] = mapped_column(
        String(10), default="free", nullable=False
    )

    declared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Nouveaux champs (v3) ───────────────────────────────────────────────
    # Horodatage exact de l'enregistrement par le comptable
    recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Qui a enregistré la cotisation (le comptable)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relations
    member: Mapped["Member"] = relationship(
        "Member", back_populates="contributions", foreign_keys=[member_id]
    )
    recorder: Mapped["Member | None"] = relationship(
        "Member", foreign_keys=[recorded_by]
    )
    transaction: Mapped["Transaction | None"] = relationship(
        back_populates="contribution"
    )

    @property
    def is_complete(self) -> bool:
        if self.contribution_mode == "fixed" and self.expected_amount:
            return float(self.amount or 0) >= float(self.expected_amount)
        return self.status == "confirmed"

    def __repr__(self) -> str:
        return (
            f"<Contribution member={self.member_id} "
            f"{self.contribution_month}/{self.contribution_year} "
            f"status={self.status}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION — Historique des messages envoyés
# ─────────────────────────────────────────────────────────────────────────────
class Notification(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "notifications"

    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    member: Mapped["Member"] = relationship(back_populates="notifications")

    def __repr__(self) -> str:
        return f"<Notification {self.type} via {self.channel} status={self.status}>"


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG — Traçabilité complète de toutes les actions
# ─────────────────────────────────────────────────────────────────────────────
class AuditLog(Base, UUIDMixin):
    __tablename__ = "audit_logs"

    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    old_values: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_values: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    member: Mapped["Member | None"] = relationship(back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} on {self.entity_type} by {self.member_id}>"