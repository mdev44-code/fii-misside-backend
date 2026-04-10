"""
auth/service.py — Logique métier de l'authentification.

Bibliothèques utilisées :
  - argon2-cffi : hashage des mots de passe avec Argon2id
  - pyjwt       : tokens JWT (via infrastructure/security/jwt.py)

API argon2-cffi :
  hasher.hash("mon_mot_de_passe")        → "$argon2id$v=19$..."
  hasher.verify(hash_stocké, "mot_passe") → True ou lève une exception
"""

from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterFromInviteRequest,
    TokenResponse,
)
from app.infrastructure.cache.redis import CacheKeys, cache_delete, cache_get, cache_set
from app.infrastructure.database.models import AuditLog, Member
from app.infrastructure.security.jwt import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.shared.enums import AuditAction, MemberStatus
from app.shared.exceptions import (
    BusinessRuleError,
    InvalidTokenError,
    NotFoundError,
    UnauthorizedError,
)

# ── Initialisation d'argon2-cffi ─────────────────────────────────────────────
# PasswordHasher() configure Argon2id avec les paramètres recommandés.
# On crée une seule instance partagée dans tout le module.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash un mot de passe avec Argon2id."""
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """
    Vérifie un mot de passe contre son hash.
    Retourne False si le mot de passe est incorrect,
    lève une exception si le hash est invalide.
    """
    try:
        return _hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHashError) as e:
        raise ValueError(f"Hash invalide : {e}") from e


class AuthService:

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ─────────────────────────────────────────────────────────────────────────
    # LOGIN
    # ─────────────────────────────────────────────────────────────────────────

    async def login(
        self,
        data: LoginRequest,
        ip_address: str | None = None,
    ) -> TokenResponse:
        """
        Connecte un membre et retourne ses tokens JWT.

        Étapes :
          1. Cherche le membre par téléphone ou email
          2. Vérifie que le compte est actif
          3. Vérifie le mot de passe avec Argon2id
          4. Génère access_token + refresh_token (PyJWT)
          5. Stocke le refresh_token dans Redis
          6. Enregistre dans les audit_logs

        Sécurité : même message d'erreur si le membre n'existe pas
        ou si le mot de passe est faux → ne révèle pas si le numéro est enregistré.
        """
        member = await self._get_by_identifier(data.identifier)

        if not member or not member.password_hash:
            raise UnauthorizedError("Identifiant ou mot de passe incorrect")

        if not verify_password(member.password_hash, data.password):
            raise UnauthorizedError("Identifiant ou mot de passe incorrect")

        if member.status != MemberStatus.ACTIVE:
            raise UnauthorizedError(
                "Votre compte est inactif. Contactez l'administrateur."
            )

        access_token = create_access_token(
            subject=str(member.id),
            extra_claims={
                "role": member.role,
                "name": member.full_name,
            },
        )
        refresh_token = create_refresh_token(subject=str(member.id))

        ttl = settings.jwt_refresh_token_expire_days * 86_400
        await cache_set(
            key=CacheKeys.refresh_token(str(member.id)),
            value=refresh_token,
            ttl_seconds=ttl,
        )

        await self._log(member.id, AuditAction.LOGIN, "member", member.id, ip_address)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            member_id=str(member.id),
            full_name=member.full_name,
            role=member.role,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # REFRESH TOKEN
    # ─────────────────────────────────────────────────────────────────────────

    async def refresh(self, refresh_token: str) -> TokenResponse:
        """
        Renouvelle l'access token à partir d'un refresh token valide.

        Rotation du refresh token : on génère un NOUVEAU refresh token
        à chaque appel. L'ancien est écrasé dans Redis.
        → Un refresh token ne peut être utilisé qu'une seule fois.
        """
        payload = decode_token(refresh_token, expected_type="refresh")
        member_id = payload.get("sub")

        if not member_id:
            raise InvalidTokenError()

        stored_token = await cache_get(CacheKeys.refresh_token(member_id))
        if stored_token != refresh_token:
            raise InvalidTokenError(
                "Refresh token révoqué. Veuillez vous reconnecter."
            )

        result = await self._db.execute(
            select(Member).where(Member.id == member_id)
        )
        member = result.scalar_one_or_none()
        if not member:
            raise NotFoundError("Membre")

        new_access = create_access_token(
            subject=str(member.id),
            extra_claims={"role": member.role, "name": member.full_name},
        )
        new_refresh = create_refresh_token(subject=str(member.id))

        ttl = settings.jwt_refresh_token_expire_days * 86_400
        await cache_set(
            key=CacheKeys.refresh_token(str(member.id)),
            value=new_refresh,
            ttl_seconds=ttl,
        )

        return TokenResponse(
            access_token=new_access,
            refresh_token=new_refresh,
            member_id=str(member.id),
            full_name=member.full_name,
            role=member.role,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # LOGOUT
    # ─────────────────────────────────────────────────────────────────────────

    async def logout(self, member_id: str, ip_address: str | None = None) -> None:
        """
        Révoque le refresh token en le supprimant de Redis.
        L'access token reste valide jusqu'à expiration (max 1h).
        """
        await cache_delete(CacheKeys.refresh_token(member_id))

        result = await self._db.execute(
            select(Member).where(Member.id == member_id)
        )
        member = result.scalar_one_or_none()
        if member:
            await self._log(
                member.id, AuditAction.LOGOUT, "member", member.id, ip_address
            )

    # ─────────────────────────────────────────────────────────────────────────
    # INSCRIPTION DEPUIS INVITATION
    # ─────────────────────────────────────────────────────────────────────────

    async def register_from_invite(
        self,
        data: RegisterFromInviteRequest,
    ) -> TokenResponse:
        """
        Crée le compte depuis un lien d'invitation.
        Double vérification : Redis (token non expiré) + BDD (statut PENDING).
        """
        cached_member_id = await cache_get(CacheKeys.invitation_token(data.token))
        if not cached_member_id:
            raise InvalidTokenError(
                "Ce lien d'invitation est invalide ou a expiré. "
                "Demandez un nouveau lien à l'administrateur."
            )

        result = await self._db.execute(
            select(Member).where(
                Member.invitation_token == data.token,
                Member.status == MemberStatus.PENDING,
            )
        )
        member = result.scalar_one_or_none()

        if not member:
            raise InvalidTokenError("Invitation déjà utilisée ou membre introuvable.")

        member.full_name = data.full_name
        member.password_hash = hash_password(data.password)
        member.status = MemberStatus.ACTIVE
        member.invitation_token = None
        member.joined_at = datetime.now(timezone.utc)

        await self._db.flush()
        await cache_delete(CacheKeys.invitation_token(data.token))

        return await self.login(
            LoginRequest(identifier=member.phone_number, password=data.password)
        )

    # ─────────────────────────────────────────────────────────────────────────
    # CHANGEMENT DE MOT DE PASSE
    # ─────────────────────────────────────────────────────────────────────────

    async def change_password(
        self,
        member: Member,
        data: ChangePasswordRequest,
    ) -> None:
        """
        Change le mot de passe. Exige l'ancien pour confirmer l'identité.
        Révoque toutes les sessions existantes après le changement.
        """
        if not verify_password(member.password_hash, data.current_password):
            raise UnauthorizedError("Mot de passe actuel incorrect")

        if verify_password(member.password_hash, data.new_password):
            raise BusinessRuleError(
                "Le nouveau mot de passe doit être différent de l'ancien"
            )

        member.password_hash = hash_password(data.new_password)
        await cache_delete(CacheKeys.refresh_token(str(member.id)))
        await self._log(member.id, AuditAction.UPDATE, "member", member.id, None)

    # ─────────────────────────────────────────────────────────────────────────
    # MÉTHODES PRIVÉES
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_by_identifier(self, identifier: str) -> Member | None:
        """Cherche un membre par téléphone OU email."""
        result = await self._db.execute(
            select(Member).where(
                or_(
                    Member.phone_number == identifier,
                    Member.email == identifier,
                )
            )
        )
        return result.scalar_one_or_none()

    async def _log(
        self,
        member_id,
        action: AuditAction,
        entity_type: str,
        entity_id,
        ip_address: str | None,
    ) -> None:
        """Enregistre une action dans les audit_logs."""
        log = AuditLog(
            member_id=member_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=ip_address,
        )
        self._db.add(log)