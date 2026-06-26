import re

from pydantic import BaseModel, field_validator

ALLOWED_COUNTRY_CODES = ("+221", "+224")


def _validate_phone(v: str) -> str:
    cleaned = v.strip().replace(" ", "").replace("-", "")

    if cleaned.startswith("+"):
        # Numéro complet fourni — vérifie l'indicatif
        if not any(cleaned.startswith(code) for code in ALLOWED_COUNTRY_CODES):
            raise ValueError("Indicatif non autorisé. Utilisez +221 (Sénégal) ou +224 (Guinée).")
        digits_after = cleaned[4:]
        if not digits_after.isdigit():
            raise ValueError("Le numéro ne doit contenir que des chiffres après l'indicatif.")
        if len(digits_after) < 7 or len(digits_after) > 10:
            raise ValueError(
                "Numéro invalide (longueur attendue : 7-10 chiffres après l'indicatif)."
            )
        return cleaned

    # Chiffres locaux — on accepte, le frontend aura déjà préfixé l'indicatif
    if not cleaned.isdigit():
        raise ValueError("Le numéro ne doit contenir que des chiffres.")
    if len(cleaned) < 7 or len(cleaned) > 10:
        raise ValueError("Numéro invalide.")
    return cleaned


def _validate_email(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip().lower()
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", v):
        raise ValueError("Format d'email invalide. Exemple : nom@domaine.com")
    return v


# ─────────────────────────────────────────────────────────────────────────────
# REQUESTS
# ─────────────────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    identifier: str
    password: str

    @field_validator("identifier")
    @classmethod
    def normalize_identifier(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def password_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Le mot de passe ne peut pas être vide")
        return v


class RefreshRequest(BaseModel):
    """Renouveler le token d'accès."""

    refresh_token: str


class RegisterFromInviteRequest(BaseModel):
    """
    Inscription depuis un lien d'invitation personnelle.
    Le token contient le rôle (stocké dans Redis).
    Le membre renseigne toutes ses informations.
    """

    token: str
    full_name: str
    phone_number: str
    email: str | None = None
    password: str

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
        return _validate_phone(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        return _validate_email(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères")
        return v


class RegisterFromGroupRequest(BaseModel):
    """
    Inscription depuis un lien d'invitation groupée.
    Le lien reste réutilisable après l'inscription.
    """

    group_token: str
    full_name: str
    phone_number: str
    email: str | None = None
    password: str

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
        return _validate_phone(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        return _validate_email(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères")
        return v


class ChangePasswordRequest(BaseModel):
    """Changer son mot de passe depuis le profil."""

    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le nouveau mot de passe doit contenir au moins 8 caractères")
        return v

class ForgotPasswordRequest(BaseModel):
    """Étape 1 : demande d'un code de réinitialisation par email."""

    email: str

    @field_validator("email")
    @classmethod
    def validate_email_required(cls, v: str) -> str:
        cleaned = _validate_email(v)
        if not cleaned:
            raise ValueError("L'email est requis.")
        return cleaned


class VerifyResetCodeRequest(BaseModel):
    """Étape 2 : vérification du code à 6 chiffres."""

    email: str
    code: str

    @field_validator("email")
    @classmethod
    def validate_email_required(cls, v: str) -> str:
        cleaned = _validate_email(v)
        if not cleaned:
            raise ValueError("L'email est requis.")
        return cleaned

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        v = v.strip()
        if not re.fullmatch(r"\d{6}", v):
            raise ValueError("Le code doit contenir exactement 6 chiffres.")
        return v


class ResetPasswordRequest(BaseModel):
    """Étape 3 : définition du nouveau mot de passe."""

    reset_token: str
    new_password: str
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        return v

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("Les deux mots de passe ne correspondent pas.")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSES
# ─────────────────────────────────────────────────────────────────────────────


class TokenResponse(BaseModel):
    """Retourné après login ou register."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    member_id: str
    full_name: str
    role: str


class MeResponse(BaseModel):
    """
    Profil du membre connecté — retourné par GET /auth/me.
    Inclut profile_picture_url pour l'affichage de l'avatar.
    """

    id: str
    full_name: str
    phone_number: str
    email: str | None
    role: str
    status: str
    joined_at: str | None
    profile_picture_url: str | None = None  # None si pas encore uploadé
