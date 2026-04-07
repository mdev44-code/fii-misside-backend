"""
auth/schemas.py — Forme des données pour le domaine authentification.

Deux catégories de schemas :
  - Request  : ce que le CLIENT envoie à l'API (corps de la requête)
  - Response : ce que l'API retourne au CLIENT

FastAPI utilise les schemas Request pour :
  1. Valider automatiquement les données (types, longueurs, formats)
  2. Générer la documentation Swagger automatiquement
  3. Retourner une erreur 422 claire si les données sont invalides

@field_validator : s'exécute automatiquement à la création de l'objet.
Si le validator lève une ValueError, Pydantic retourne une erreur 422
avec le message de l'erreur — avant même d'appeler le service.
"""

from pydantic import BaseModel, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# REQUESTS (données envoyées par le client)
# ─────────────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    """
    Données attendues pour se connecter.

    'identifier' accepte soit le numéro de téléphone, soit l'email.
    On normalise en minuscules pour éviter les problèmes de casse
    ("Mamadou@gmail.com" et "mamadou@gmail.com" doivent fonctionner).
    """
    identifier: str   # téléphone (+221XXXXXXXX) ou email
    password: str

    @field_validator("identifier")
    @classmethod
    def normalize_identifier(cls, v: str) -> str:
        """
        @classmethod : reçoit la classe (cls) et la valeur brute (v).
        strip() supprime les espaces avant/après.
        lower() met en minuscules.
        """
        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def password_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Le mot de passe ne peut pas être vide")
        return v


class RefreshRequest(BaseModel):
    """Données pour renouveler le token d'accès."""
    refresh_token: str


class RegisterFromInviteRequest(BaseModel):
    """
    Données pour créer son compte depuis un lien d'invitation.

    Le token est inclus dans le lien envoyé par l'admin :
    https://app.association.com/register?token=abc123

    Le frontend extrait le token de l'URL et l'inclut dans cette requête.
    """
    token: str          # token d'invitation (extrait de l'URL)
    full_name: str      # le membre saisit son propre nom
    password: str       # il choisit son mot de passe

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Le nom complet doit contenir au moins 2 caractères")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """
        Règles minimales de sécurité pour le mot de passe.
        On peut en ajouter d'autres (majuscule, chiffre...) si besoin.
        """
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères")
        return v


class ChangePasswordRequest(BaseModel):
    """Données pour changer son mot de passe depuis le profil."""
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le nouveau mot de passe doit contenir au moins 8 caractères")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSES (données retournées par l'API)
# ─────────────────────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    """
    Retourné après un login ou un register réussi.

    On inclut role et full_name directement dans la réponse pour que
    le frontend puisse afficher le bon menu/interface sans faire
    une deuxième requête vers /auth/me.
    """
    access_token: str    # courte durée (1h) — utilisé dans chaque requête
    refresh_token: str   # longue durée (30j) — pour régénérer l'access token
    token_type: str = "bearer"
    member_id: str
    full_name: str
    role: str


class MeResponse(BaseModel):
    """
    Profil du membre connecté — retourné par GET /auth/me.

    On n'expose jamais password_hash ni invitation_token — ce sont
    des champs internes qui ne doivent pas sortir de l'API.
    """
    id: str
    full_name: str
    phone_number: str
    email: str | None
    role: str
    status: str
    joined_at: str | None