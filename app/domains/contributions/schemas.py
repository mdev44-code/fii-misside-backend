"""
contributions/schemas.py — Forme des données pour le domaine cotisations.

Ce domaine gère le suivi mensuel des cotisations par membre.
Il s'appuie sur les settings de l'association pour connaître le mode
(libre ou fixe) et le montant attendu.
"""

from pydantic import BaseModel


# ─────────────────────────────────────────────────────────────────────────────
# REQUESTS
# ─────────────────────────────────────────────────────────────────────────────

class DeclareContributionRequest(BaseModel):
    """
    Le membre déclare qu'il a envoyé sa cotisation via Wave.

    Aucun champ obligatoire côté membre — le mois/année est déterminé
    automatiquement par le service (mois courant).
    Le montant est renseigné par le comptable lors de la confirmation.

    Ce schema existe pour permettre des extensions futures
    (ex: message optionnel au comptable).
    """
    pass  # intentionnellement vide


class UpdateAssociationSettingsRequest(BaseModel):
    """
    Modification des paramètres de cotisation par l'admin.

    contribution_mode : "free" ou "fixed"
    fixed_amount      : obligatoire si mode = "fixed", ignoré si "free"

    Exemple d'usage :
      Passer en mode fixe à 5000 FCFA :
      { "contribution_mode": "fixed", "fixed_amount": 5000 }

      Repasser en mode libre :
      { "contribution_mode": "free" }
    """
    contribution_mode: str
    fixed_amount: float | None = None

    def model_post_init(self, __context) -> None:
        """
        model_post_init : s'exécute après la création de l'objet Pydantic.
        Permet de faire des validations qui dépendent de plusieurs champs.

        Si mode = "fixed" et pas de montant → erreur.
        """
        if self.contribution_mode == "fixed" and not self.fixed_amount:
            raise ValueError(
                "Le montant fixe est obligatoire en mode 'fixed'"
            )
        if self.contribution_mode not in {"free", "fixed"}:
            raise ValueError(
                "Mode invalide. Valeurs acceptées : 'free' ou 'fixed'"
            )


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSES
# ─────────────────────────────────────────────────────────────────────────────

class ContributionStatusResponse(BaseModel):
    """
    Statut de cotisation d'un membre pour un mois donné.
    Utilisé dans le tableau récapitulatif mensuel.
    """
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
    """
    Rapport mensuel complet des cotisations.

    Contient le récapitulatif par membre + des statistiques globales.
    """
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
    """
    Retourné au membre après qu'il a déclaré sa cotisation.
    Contient les informations pour effectuer le transfert Wave.
    """
    contribution_id: str
    status: str
    wave_number: str      # numéro Wave du comptable à contacter
    amount_to_send: float | None  # null en mode libre
    contribution_mode: str
    message: str          # message explicatif pour le membre


class AssociationSettingsResponse(BaseModel):
    """Paramètres actuels de l'association."""
    contribution_mode: str
    fixed_amount: float | None
    currency: str
    updated_at: str | None
    updated_by_name: str | None