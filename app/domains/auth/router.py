from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    RegisterFromGroupRequest,
    RegisterFromInviteRequest,
    ResetPasswordRequest,
    VerifyResetCodeRequest,
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


# ─────────────────────────────────────────────────────────────────────────────
# MOT DE PASSE OUBLIÉ (routes publiques — pas de get_current_member)
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Étape 1 — Envoie un code OTP si un compte existe pour cet email."""
    service = AuthService(db)
    result = await service.request_password_reset(data)
    return success_response(
        data=result,
        message=(
            "Si un compte est associé à cet email, un code de vérification vient d'être envoyé."
        ),
    )


@router.post("/verify-reset-code")
async def verify_reset_code(
    data: VerifyResetCodeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Étape 2 — Vérifie le code et renvoie un reset_token."""
    service = AuthService(db)
    result = await service.verify_reset_code(data)
    return success_response(data=result, message="Code vérifié.")


@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Étape 3 — Définit le nouveau mot de passe."""
    service = AuthService(db)
    await service.reset_password(data)
    return success_response(
        message=("Mot de passe réinitialisé avec succès. Vous pouvez maintenant vous connecter."),
    )
