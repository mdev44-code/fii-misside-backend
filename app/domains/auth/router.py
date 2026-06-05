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
    RegisterFromGroupRequest,
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
    data: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    ip = request.client.host if request.client else None
    tokens = await service.login(data, ip_address=ip)
    return success_response(data=tokens.model_dump(), message="Connexion réussie")


# ─────────────────────────────────────────────────────────────────────────────
# GET /auth/group-invite/{token} — Valider un token de groupe (public)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/group-invite/{token}")
async def validate_group_invite(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    result = await service.validate_group_token(token)
    return success_response(data=result)


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/register-group — S'inscrire via lien groupé (public)
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/register-group")
async def register_from_group(
    data: RegisterFromGroupRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Crée un compte depuis un lien d'invitation groupée.
    Route publique. Le token reste valide après l'inscription.
    """
    service = AuthService(db)
    tokens = await service.register_from_group(data)
    return success_response(
        data=tokens.model_dump(),
        message="Compte créé avec succès. Bienvenue dans l'association !",
    )


@router.post("/refresh")
async def refresh_token(
    data: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    tokens = await service.refresh(data.refresh_token)
    return success_response(data=tokens.model_dump(), message="Token renouvelé")


@router.post("/logout")
async def logout(
    request: Request,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
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
    Crée un compte depuis un lien d'invitation personnelle.
    Route publique. Le token est à usage unique.
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
    service = AuthService(db)
    await service.change_password(current_member, data)
    return success_response(message="Mot de passe modifié. Veuillez vous reconnecter.")


@router.get("/me")
async def get_me(
    current_member=Depends(get_current_member),
):
    return success_response(
        data=MeResponse(
            id=str(current_member.id),
            full_name=current_member.full_name,
            phone_number=current_member.phone_number,
            email=current_member.email,
            role=current_member.role,
            status=current_member.status,
            joined_at=(current_member.joined_at.isoformat() if current_member.joined_at else None),
            profile_picture_url=current_member.profile_picture_url,
        ).model_dump()
    )
