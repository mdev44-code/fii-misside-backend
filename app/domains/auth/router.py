"""
auth/router.py — Endpoints HTTP de l'authentification.

Ce fichier ne contient AUCUNE logique métier.
Son rôle se limite à :
  1. Déclarer les routes (méthode HTTP + URL)
  2. Recevoir les données (validées automatiquement par les schemas)
  3. Injecter les dépendances (session DB, membre connecté)
  4. Appeler le service
  5. Retourner la réponse formatée

Routes exposées :
  POST /api/v1/auth/login        → connexion
  POST /api/v1/auth/refresh      → renouveler le token
  POST /api/v1/auth/logout       → déconnexion
  POST /api/v1/auth/register     → créer compte depuis invitation
  PUT  /api/v1/auth/password     → changer son mot de passe
  GET  /api/v1/auth/me           → profil du membre connecté
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    RegisterFromInviteRequest,
)
from app.domains.auth.service import AuthService
from app.infrastructure.database.session import get_db
from app.shared.response import success_response

# APIRouter : un groupe de routes.
# prefix et tags sont ajoutés dans main.py au moment de l'inclusion.
router = APIRouter()


@router.post("/login")
async def login(
    data: LoginRequest,       # corps JSON validé automatiquement par Pydantic
    request: Request,          # objet Request FastAPI (pour récupérer l'IP)
    db: AsyncSession = Depends(get_db),
):
    """
    Connecte un membre avec son téléphone/email et son mot de passe.

    Retourne access_token + refresh_token.

    Request est injecté par FastAPI — il contient les infos de la requête HTTP
    (IP, headers, etc.). On l'utilise uniquement pour logger l'IP dans l'audit.
    """
    service = AuthService(db)
    # request.client est None si l'IP n'est pas disponible (tests, proxy)
    ip = request.client.host if request.client else None
    tokens = await service.login(data, ip_address=ip)

    return success_response(
        data=tokens.model_dump(),
        message="Connexion réussie",
    )


@router.post("/refresh")
async def refresh_token(
    data: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Renouvelle l'access token à partir d'un refresh token valide.
    Le frontend appelle cette route automatiquement quand il reçoit
    une erreur 401 (token expiré).
    """
    service = AuthService(db)
    tokens = await service.refresh(data.refresh_token)

    return success_response(
        data=tokens.model_dump(),
        message="Token renouvelé",
    )


@router.post("/logout")
async def logout(
    request: Request,
    current_member=Depends(get_current_member),  # vérifie le token
    db: AsyncSession = Depends(get_db),
):
    """
    Déconnecte le membre en révoquant son refresh token.
    Nécessite d'être connecté (Depends(get_current_member)).
    """
    service = AuthService(db)
    ip = request.client.host if request.client else None
    await service.logout(str(current_member.id), ip_address=ip)

    return success_response(message="Déconnexion réussie")


@router.post("/register")
async def register_from_invite(
    data: RegisterFromInviteRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Crée un compte depuis un lien d'invitation.
    Route publique : pas besoin d'être connecté.
    """
    service = AuthService(db)
    tokens = await service.register_from_invite(data)

    return success_response(
        data=tokens.model_dump(),
        message="Compte créé avec succès. Bienvenue !",
    )


@router.put("/password")
async def change_password(
    data: ChangePasswordRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Change le mot de passe du membre connecté.
    Exige l'ancien mot de passe pour confirmer l'identité.
    """
    service = AuthService(db)
    await service.change_password(current_member, data)

    return success_response(
        message="Mot de passe modifié. Veuillez vous reconnecter."
    )


@router.get("/me")
async def get_me(
    current_member=Depends(get_current_member),
):
    """
    Retourne le profil du membre connecté.

    Note : pas de db ici car current_member est déjà chargé
    par la dependency get_current_member. Pas besoin d'une
    deuxième requête BDD.

    On utilise MeResponse pour contrôler exactement quels champs
    sont exposés (pas de password_hash, pas d'invitation_token).
    """
    return success_response(
        data=MeResponse(
            id=str(current_member.id),
            full_name=current_member.full_name,
            phone_number=current_member.phone_number,
            email=current_member.email,
            role=current_member.role,
            status=current_member.status,
            joined_at=(
                current_member.joined_at.isoformat()
                if current_member.joined_at
                else None
            ),
        ).model_dump()
    )