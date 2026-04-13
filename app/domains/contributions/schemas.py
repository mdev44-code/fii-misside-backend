"""
contributions/schemas.py — Forme des données pour le domaine cotisations.

Ce domaine gère le suivi mensuel des cotisations par membre.
Il s'appuie sur les settings de l'association pour connaître le mode
(libre ou fixe) et le montant attendu.
"""

from pydantic import BaseModel, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# REQUESTS
# ─────────────────────────────────────────────────────────────────────────────

class DeclareContributionRequest(BaseModel):
    pass  # intentionnellement vide


class UpdateAssociationSettingsRequest(BaseModel):
    contribution_mode: str
    fixed_amount: float | None = None

    def model_post_init(self, __context) -> None:
        if self.contribution_mode == "fixed" and not self.fixed_amount:
            raise ValueError(
                "Le montant fixe est obligatoire en mode 'fixed'"
            )
        if self.contribution_mode not in {"free", "fixed"}:
            raise ValueError(
                "Mode invalide. Valeurs acceptées : 'free' ou 'fixed'"
            )


class TreasurerAddContributionRequest(BaseModel):
    member_id: str
    amount: float | None = None         # Obligatoire en mode free, ignoré en mode fixed
    contribution_month: int | None = None  # Optionnel : par défaut mois courant
    contribution_year: int | None = None   # Optionnel : par défaut année courante
 
    @field_validator("member_id")
    @classmethod
    def member_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("L'identifiant du membre est requis")
        return v.strip()
 
    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("Le montant doit être supérieur à 0")
        return v
 
    @field_validator("contribution_month")
    @classmethod
    def month_valid(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 12):
            raise ValueError("Le mois doit être entre 1 et 12")
        return v
 
    @field_validator("contribution_year")
    @classmethod
    def year_valid(cls, v: int | None) -> int | None:
        if v is not None and not (2020 <= v <= 2100):
            raise ValueError("L'année est invalide")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSES
# ─────────────────────────────────────────────────────────────────────────────

class ContributionStatusResponse(BaseModel):
    member_id: str
    full_name: str
    phone_number: str
    status: str           # pending | declared | confirmed | late
    amount: float | None  # null si pas encore confirmé
    expected_amount: float | None  # null en mode libre
    contribution_mode: str
    declared_at: str | None
    confirmed_at: str | None
    is_complete: bool     # True si cotisation complète (mode fixe)


class MonthlyReportResponse(BaseModel):
    month: int
    year: int
    contribution_mode: str
    fixed_amount: float | None
    members: list[ContributionStatusResponse]

    # Statistiques calculées
    total_members: int
    confirmed_count: int    # ont cotisé et confirmé
    declared_count: int     # ont déclaré mais pas encore confirmé
    pending_count: int      # n'ont rien fait
    late_count: int         # en retard (période dépassée)
    total_collected: float  # somme des montants confirmés ce mois


class DeclareContributionResponse(BaseModel):
    contribution_id: str
    status: str
    wave_number: str      # numéro Wave du comptable à contacter
    amount_to_send: float | None  # null en mode libre
    contribution_mode: str
    message: str          # message explicatif pour le membre


class AssociationSettingsResponse(BaseModel):
    contribution_mode: str
    fixed_amount: float | None
    currency: str
    updated_at: str | None
    updated_by_name: str | None


class ContributionResponse(BaseModel):
    id: str
    member_id: str
    member_name: str
    contribution_month: int
    contribution_year: int
    status: str
    amount: float | None
    expected_amount: float | None
    contribution_mode: str
    recorded_at: str | None       # ISO 8601 — heure exacte d'enregistrement
    recorded_by_name: str | None  # Nom du comptable enregistreur
    confirmed_at: str | None
    created_at: str
 
    @classmethod
    def from_model(
        cls,
        contribution,
        member_name: str = "",
        recorded_by_name: str | None = None,
    ) -> "ContributionResponse":
        return cls(
            id=str(contribution.id),
            member_id=str(contribution.member_id),
            member_name=member_name,
            contribution_month=contribution.contribution_month,
            contribution_year=contribution.contribution_year,
            status=contribution.status,
            amount=float(contribution.amount) if contribution.amount else None,
            expected_amount=float(contribution.expected_amount) if contribution.expected_amount else None,
            contribution_mode=contribution.contribution_mode,
            recorded_at=contribution.recorded_at.isoformat() if contribution.recorded_at else None,
            recorded_by_name=recorded_by_name,
            confirmed_at=contribution.confirmed_at.isoformat() if contribution.confirmed_at else None,
            created_at=contribution.created_at.isoformat(),
        )
