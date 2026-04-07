"""
treasury/schemas.py — Forme des données pour le domaine caisse.

Ce domaine gère trois types d'opérations :
  1. Initialisation   : le comptable synchronise la caisse avec l'existant
  2. Dépôt            : confirmation d'une cotisation Wave reçue
  3. Dépense          : enregistrement d'un retrait Wave effectué

Règle importante sur les montants :
  Tous les montants sont en FCFA (nombre entier ou décimal à 2 chiffres max).
  On utilise float dans les schemas mais Numeric(12,2) en BDD pour éviter
  les erreurs d'arrondi des floats sur les gros montants.
"""

from pydantic import BaseModel, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# REQUESTS
# ─────────────────────────────────────────────────────────────────────────────

class InitBalanceRequest(BaseModel):
    """
    Initialisation de la caisse par le comptable.
    Saisie unique pour synchroniser l'app avec la caisse physique existante.
    """
    initial_balance: float

    @field_validator("initial_balance")
    @classmethod
    def must_be_positive_or_zero(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Le solde initial ne peut pas être négatif")
        return v


class ConfirmDepositRequest(BaseModel):
    """
    Confirmation d'une cotisation reçue via Wave.
    Appelée par le comptable après avoir vu le transfert sur son Wave.

    member_id      : qui a cotisé
    amount         : montant réellement reçu (peut différer en mode libre)
    contribution_id: optionnel — pour lier à une contribution déclarée existante
    description    : note optionnelle (ex: "cotisation juillet 2025")
    """
    member_id: str
    amount: float
    contribution_id: str | None = None
    description: str | None = None

    @field_validator("amount")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Le montant doit être supérieur à 0")
        return v


class ExpenseRequest(BaseModel):
    """
    Enregistrement d'une dépense après retrait Wave physique.

    amount     : montant dépensé
    description: obligatoire — explique pourquoi cet argent a été dépensé
    project_id : optionnel — lié à un projet si c'est pour un projet
                 null si c'est une dépense générale (frais, fournitures...)
    """
    amount: float
    description: str
    project_id: str | None = None

    @field_validator("amount")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Le montant doit être supérieur à 0")
        return v

    @field_validator("description")
    @classmethod
    def description_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("La description est obligatoire pour une dépense")
        if len(v) < 5:
            raise ValueError("La description doit faire au moins 5 caractères")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSES
# ─────────────────────────────────────────────────────────────────────────────

class BalanceResponse(BaseModel):
    """
    Solde actuel de la caisse.
    Retourné par GET /treasury/balance.
    """
    balance: float
    initial_balance: float
    initialized_at: str | None
    initialized_by_name: str | None  # nom du comptable qui a initialisé


class TransactionResponse(BaseModel):
    """
    Représentation d'une transaction (dépôt, dépense ou ajustement).

    balance_after : solde APRÈS cette transaction — permet de voir
                    l'évolution du solde au fil du temps dans l'historique.
    member_name   : nom du membre concerné (pour les dépôts)
    project_title : titre du projet concerné (pour les dépenses liées)
    """
    id: str
    type: str
    amount: float
    balance_after: float
    description: str | None
    member_name: str | None     # null pour les dépenses et ajustements
    project_title: str | None   # null si pas lié à un projet
    performed_by_name: str      # comptable qui a enregistré
    status: str
    performed_at: str

    @classmethod
    def from_model(cls, tx, member_name=None, project_title=None, performed_by_name="") -> "TransactionResponse":
        """
        Convertit un objet Transaction SQLAlchemy en TransactionResponse.
        Les noms sont passés en paramètre car ils viennent de jointures
        que le service a résolues.
        """
        return cls(
            id=str(tx.id),
            type=tx.type,
            amount=float(tx.amount),
            balance_after=float(tx.balance_after),
            description=tx.description,
            member_name=member_name,
            project_title=project_title,
            performed_by_name=performed_by_name,
            status=tx.status,
            performed_at=tx.performed_at.isoformat(),
        )