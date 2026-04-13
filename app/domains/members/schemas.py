"""
members/schemas.py — Forme des données pour le domaine membres.

Ce domaine gère :
  - L'invitation d'un nouveau membre par l'admin
  - L'affichage de la liste des membres et de l'organigramme
  - La modification du rôle ou du statut d'un membre

Règle de sécurité appliquée ici :
  Les schemas Response ne contiennent JAMAIS password_hash ni invitation_token.
  Ces champs existent dans le modèle SQLAlchemy (BDD) mais ne doivent
  jamais sortir de l'API — on les exclut en ne les déclarant pas dans les schemas.
"""

from pydantic import BaseModel, field_validator

from app.shared.enums import Role


# ─────────────────────────────────────────────────────────────────────────────
# REQUESTS
# ─────────────────────────────────────────────────────────────────────────────

class InviteMemberRequest(BaseModel):
    """
    Données pour inviter un nouveau membre.
    Envoyées par l'admin via POST /members/invite.

    Le numéro de téléphone est obligatoire (c'est l'identifiant principal).
    L'email est optionnel — str | None signifie que le champ peut être absent.
    """
    full_name: str
    phone_number: str
    email: str | None = None
    role: Role = Role.MEMBER  # par défaut : membre simple

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Le nom doit contenir au moins 2 caractères")
        return v

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """
        Normalise et valide le format du numéro.
        On exige l'indicatif pays pour éviter les ambiguités.
        Ex : "+221771234567" ✅ / "771234567" ❌
        """
        v = v.strip().replace(" ", "")
        if not v.startswith("+"):
            raise ValueError(
                "Le numéro doit inclure l'indicatif pays (ex: +221771234567)"
            )
        if len(v) < 10:
            raise ValueError("Numéro de téléphone trop court")
        return v


class UpdateMemberRoleRequest(BaseModel):
    """Données pour changer le rôle d'un membre."""
    role: Role


class UpdateMemberStatusRequest(BaseModel):
    """Données pour activer, désactiver ou suspendre un membre."""
    status: str  # active | inactive | suspended

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"active", "inactive", "suspended"}
        if v not in allowed:
            raise ValueError(f"Statut invalide. Valeurs acceptées : {allowed}")
        return v


class UpdateProfileRequest(BaseModel):
    """
    Modification partielle du profil du membre connecté.
 
    Tous les champs sont optionnels — on n'envoie que ce qu'on veut changer.
    Pattern PATCH : seuls les champs présents dans le body sont modifiés.
 
    Règles métier :
      - full_name  : min 2 caractères, pas vide
      - email      : format email valide, unicité vérifiée dans le service
      - phone_number : unicité vérifiée dans le service
 
    Exemple :
      PATCH /api/v1/members/me
      Body : {"email": "nouveau@email.com"}
      → Seul l'email est modifié, le reste reste inchangé.
    """
    full_name: str | None = None
    email: str | None = None
    phone_number: str | None = None
 
    @field_validator("full_name")
    @classmethod
    def name_not_empty(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if len(v) < 2:
                raise ValueError("Le nom doit contenir au moins 2 caractères")
        return v
 
    @field_validator("phone_number")
    @classmethod
    def phone_not_empty(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if len(v) < 8:
                raise ValueError("Le numéro de téléphone est invalide")
        return v
 
    @field_validator("email")
    @classmethod
    def email_format(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip().lower()
            if "@" not in v or "." not in v:
                raise ValueError("Format d'email invalide")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSES
# ─────────────────────────────────────────────────────────────────────────────

class MemberResponse(BaseModel):
    id: str
    full_name: str
    email: str | None
    phone_number: str
    role: str
    status: str
    profile_picture_url: str | None
    joined_at: str | None
    created_at: str
 
    @classmethod
    def from_model(cls, member) -> "MemberResponse":
        return cls(
            id=str(member.id),
            full_name=member.full_name,
            email=member.email,
            phone_number=member.phone_number,
            role=member.role,
            status=member.status,
            profile_picture_url=member.profile_picture_url,
            joined_at=member.joined_at.isoformat() if member.joined_at else None,
            created_at=member.created_at.isoformat(),
        )


class InviteMemberResponse(BaseModel):
    """
    Retourné après la création d'une invitation.
    Contient le lien d'invitation à partager avec le nouveau membre.

    Le lien ressemble à :
    https://app.asso.com/register?token=abc123xyz
    L'admin le copie et l'envoie par WhatsApp ou SMS au nouveau membre.
    """
    member: MemberResponse
    invitation_link: str
    message: str  # message explicatif pour l'admin


class OrgChartMemberResponse(BaseModel):
    """
    Version allégée pour l'organigramme — moins de champs que MemberResponse.
    On n'expose pas le statut ni la date d'inscription dans l'organigramme.
    """
    id: str
    full_name: str
    phone_number: str
    role: str

    @classmethod
    def from_model(cls, member) -> "OrgChartMemberResponse":
        return cls(
            id=str(member.id),
            full_name=member.full_name,
            phone_number=member.phone_number,
            role=member.role,
        )


class OrgChartResponse(BaseModel):
    """
    Structure de l'organigramme groupée par rôle.

    Exemple de réponse JSON :
    {
        "admin"    : [{"id": "...", "full_name": "Mamadou", ...}],
        "treasurer": [{"id": "...", "full_name": "Aminata", ...}],
        "manager"  : [],
        "member"   : [{"id": "...", ...}, {"id": "...", ...}]
    }

    Le frontend peut ainsi afficher chaque groupe dans une section dédiée.
    """
    admin: list[OrgChartMemberResponse]
    treasurer: list[OrgChartMemberResponse]
    manager: list[OrgChartMemberResponse]
    member: list[OrgChartMemberResponse]