"""
database/base.py — Classes de base pour tous les modèles SQLAlchemy.

Un modèle SQLAlchemy = une classe Python qui représente une table en base.
Chaque attribut de classe = une colonne dans la table.

Plutôt que de répéter id/created_at/updated_at dans chaque modèle,
on les définit une fois ici dans des Mixins.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Classe mère de tous les modèles.
    DeclarativeBase est le système de SQLAlchemy qui fait le lien
    entre une classe Python et une table PostgreSQL.

    Toutes les tables héritent de Base :
        class Member(Base, UUIDMixin, TimestampMixin):
            __tablename__ = "members"
            ...
    """

    pass


class UUIDMixin:
    """
    Ajoute un identifiant UUID à chaque modèle.

    Pourquoi UUID plutôt qu'un entier auto-incrémenté ?
    - Un entier (1, 2, 3...) expose le nombre de lignes de ta table
    - Un UUID est impossible à deviner : pas de /users/1 → /users/2
    - Pratique pour créer des enregistrements côté client avant insertion en BDD

    Mapped[uuid.UUID] = type Python de la colonne
    mapped_column(...)  = définition de la colonne SQL
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),  # stocké comme vrai UUID en PostgreSQL
        primary_key=True,
        default=uuid.uuid4,  # généré automatiquement si non fourni
        nullable=False,
    )


class TimestampMixin:
    """
    Ajoute created_at et updated_at à chaque modèle.

    server_default=func.now() : PostgreSQL calcule la valeur côté serveur
    onupdate=func.now()       : PostgreSQL met à jour automatiquement à chaque UPDATE

    Avantage : même si tu oublies de fournir ces valeurs depuis Python,
    la base de données les gère elle-même.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
