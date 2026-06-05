from pydantic import BaseModel, Field, field_validator

from app.shared.enums import Role

# ─────────────────────────────────────────────────────────────────────────────
# REQUESTS
# ─────────────────────────────────────────────────────────────────────────────


class InviteMemberRequest(BaseModel):
    """
    Données pour créer une invitation individuelle.
    v2 : l'admin ne saisit plus les infos du membre, uniquement le rôle.
    """

    role: Role = Role.MEMBER


class UpdateMemberRoleRequest(BaseModel):
    """Données pour changer le rôle d'un membre."""

    role: Role
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
    Pattern PATCH : seuls les champs présents dans le body sont modifiés.
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
            v = v.strip().replace(" ", "")
            if len(v) < 8:
                raise ValueError("Le numéro de téléphone est invalide")
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
    Retourné après la création d'une invitation individuelle.
    v2 : plus de champ 'member' — le membre n'est pas encore créé en BDD.
    """

    invitation_link: str
    message: str


class OrgChartMemberResponse(BaseModel):
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
    """Structure de l'organigramme groupée par rôle."""

    admin: list[OrgChartMemberResponse]
    treasurer: list[OrgChartMemberResponse]
    manager: list[OrgChartMemberResponse]
    member: list[OrgChartMemberResponse]


# ─────────────────────────────────────────────────────────────────────────────
# REQUÊTES (ce que l'admin envoie)
# ─────────────────────────────────────────────────────────────────────────────


class CreateGroupInviteRequest(BaseModel):
    label: str | None = Field(
        default=None,
        max_length=150,
        description="Label lisible pour identifier ce lien (ex: 'Groupe WhatsApp Mars')",
        examples=["Groupe WhatsApp Mars 2026"],
    )
    default_role: str = Field(
        default="member",
        description="Rôle attribué aux inscrits via ce lien",
        examples=["member", "manager"],
    )
    expires_in_hours: int = Field(
        default=168,  # 7 jours par défaut
        ge=1,
        le=720,  # max 30 jours
        description="Durée de validité du lien en heures (1h → 720h / 30 jours)",
        examples=[168, 72, 48],
    )
    max_uses: int | None = Field(
        default=None,
        ge=1,
        description="Nombre max d'inscriptions via ce lien (None = illimité)",
        examples=[20, 50, None],
    )


# ─────────────────────────────────────────────────────────────────────────────
# RÉPONSES (ce que l'API retourne)
# ─────────────────────────────────────────────────────────────────────────────


class GroupInviteResponse(BaseModel):
    """
    Représentation d'un lien d'invitation groupé.
    Retourné après création et dans la liste.
    """

    id: str
    token: str
    label: str | None
    default_role: str
    expires_at: str  # ISO 8601
    max_uses: int | None
    use_count: int
    is_active: bool
    invitation_link: str  # URL complète à partager
    created_at: str

    @classmethod
    def from_model(cls, invite, frontend_url: str) -> "GroupInviteResponse":
        return cls(
            id=str(invite.id),
            token=invite.token,
            label=invite.label,
            default_role=invite.default_role,
            expires_at=invite.expires_at.isoformat(),
            max_uses=invite.max_uses,
            use_count=invite.use_count,
            is_active=invite.is_active,
            invitation_link=f"{frontend_url}/register?group={invite.token}",
            created_at=invite.created_at.isoformat(),
        )


class GroupInviteValidationResponse(BaseModel):
    """
    Réponse à la validation d'un token de groupe.
    Le frontend appelle cette route avant d'afficher le formulaire
    d'inscription pour vérifier que le lien est encore valide.
    """

    is_valid: bool
    default_role: str
    label: str | None
    expires_at: str
    message: str


# ─────────────────────────────────────────────────────────────────────────────
# INSCRIPTION VIA LIEN GROUPÉ
# ─────────────────────────────────────────────────────────────────────────────


class RegisterFromGroupInviteRequest(BaseModel):
    """
    Corps de la requête d'inscription via lien groupé.

    Contrairement à l'invitation personnelle (où le membre est pré-créé
    avec un nom et un téléphone connus), ici la personne remplit
    TOUTES ses informations elle-même.

    Exemple JSON :
    {
        "group_token": "Dq3mK9vL2nXpRtYz...",
        "full_name": "Mamadou Diallo",
        "phone_number": "+221771234567",
        "email": "mamadou@example.com",
        "password": "motdepasse123"
    }
    """

    group_token: str = Field(
        description="Token du lien d'invitation groupé",
    )
    full_name: str = Field(
        min_length=2,
        max_length=150,
        description="Nom complet du nouveau membre",
        examples=["Mamadou Diallo"],
    )
    phone_number: str = Field(
        min_length=8,
        max_length=20,
        description="Numéro de téléphone (identifiant principal)",
        examples=["+221771234567"],
    )
    email: str | None = Field(
        default=None,
        max_length=255,
        description="Email (optionnel)",
        examples=["mamadou@example.com"],
    )
    password: str = Field(
        min_length=8,
        max_length=128,
        description="Mot de passe (minimum 8 caractères)",
    )
