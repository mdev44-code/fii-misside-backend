"""
auth/service.py — Logique métier de l'authentification.

Corrections v3 :
  - register_from_invite : ne rappelle plus login() après flush() car le membre
    n'est pas encore commis en BDD → génère les tokens directement depuis
    l'objet membre en mémoire, comme le fait login() mais sans requête BDD.
  - validate_group_token : nouvelle méthode pour vérifier un token de groupe.
  - Imports : ajout RegisterFromGroupRequest et Role.
"""

import json
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterFromGroupRequest,
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
from app.shared.enums import AuditAction, MemberStatus, Role
from app.shared.exceptions import (
    BusinessRuleError,
    ConflictError,
    InvalidTokenError,
    NotFoundError,
    UnauthorizedError,
)

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
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
        member = await self._get_by_identifier(data.identifier)
        if not member:
            raise UnauthorizedError("Identifiant ou mot de passe incorrect")

        if member.status != MemberStatus.ACTIVE:
            raise UnauthorizedError(
                "Votre compte est inactif ou suspendu. Contactez l'administrateur."
            )

        if not verify_password(member.password_hash, data.password):
            raise UnauthorizedError("Identifiant ou mot de passe incorrect")

        tokens = await self._create_tokens_for(member)
        await self._log(member.id, AuditAction.LOGIN, "member", member.id, ip_address)
        return tokens

    # ─────────────────────────────────────────────────────────────────────────
    # REFRESH
    # ─────────────────────────────────────────────────────────────────────────

    async def refresh(self, refresh_token: str) -> TokenResponse:
        payload   = decode_token(refresh_token, expected_type="refresh")
        member_id = payload.get("sub")
        if not member_id:
            raise InvalidTokenError()

        stored_token = await cache_get(CacheKeys.refresh_token(member_id))
        if stored_token != refresh_token:
            raise InvalidTokenError("Refresh token révoqué. Veuillez vous reconnecter.")

        result = await self._db.execute(
            select(Member).where(Member.id == member_id)
        )
        member = result.scalar_one_or_none()
        if not member:
            raise NotFoundError("Membre")

        return await self._create_tokens_for(member)

    # ─────────────────────────────────────────────────────────────────────────
    # LOGOUT
    # ─────────────────────────────────────────────────────────────────────────

    async def logout(self, member_id: str, ip_address: str | None = None) -> None:
        await cache_delete(CacheKeys.refresh_token(member_id))
        result = await self._db.execute(
            select(Member).where(Member.id == member_id)
        )
        member = result.scalar_one_or_none()
        if member:
            await self._log(member.id, AuditAction.LOGOUT, "member", member.id, ip_address)

    # ─────────────────────────────────────────────────────────────────────────
    # INSCRIPTION DEPUIS INVITATION PERSONNELLE (v3 — sans appel à login())
    # ─────────────────────────────────────────────────────────────────────────

    async def register_from_invite(
        self,
        data: RegisterFromInviteRequest,
    ) -> TokenResponse:
        """
        Crée le compte depuis une invitation personnelle (one-time token).

        IMPORTANT : on ne rappelle PAS self.login() après flush() car le membre
        n'est pas encore commis en BDD à ce stade — _get_by_identifier() ne le
        trouverait pas et retournerait None → UnauthorizedError.

        On génère les tokens directement depuis l'objet membre en mémoire
        via _create_tokens_for(), exactement comme login() mais sans requête BDD.
        """
        # 1. Vérifie et décode le token Redis
        cached_value = await cache_get(CacheKeys.invitation_token(data.token))
        if not cached_value:
            raise InvalidTokenError(
                "Ce lien d'invitation est invalide ou a expiré. "
                "Demandez un nouveau lien à l'administrateur."
            )

        try:
            token_data = json.loads(cached_value)
        except (json.JSONDecodeError, TypeError):
            raise InvalidTokenError("Token d'invitation corrompu.")

        if token_data.get("type") != "personal":
            raise InvalidTokenError(
                "Ce lien est un lien groupé. Utilisez le bon format d'URL."
            )

        try:
            role = Role(token_data.get("role", "member"))
        except ValueError:
            role = Role.MEMBER

        # 2. Unicité téléphone
        existing_phone = await self._db.execute(
            select(Member).where(Member.phone_number == data.phone_number)
        )
        if existing_phone.scalar_one_or_none():
            raise ConflictError(
                f"Un compte avec le numéro {data.phone_number} existe déjà."
            )

        # 3. Unicité email
        if data.email:
            existing_email = await self._db.execute(
                select(Member).where(Member.email == data.email)
            )
            if existing_email.scalar_one_or_none():
                raise ConflictError(
                    f"Un compte avec l'email {data.email} existe déjà."
                )

        # 4. Crée le membre
        member = Member(
            full_name=data.full_name,
            phone_number=data.phone_number,
            email=data.email,
            role=role,
            status=MemberStatus.ACTIVE,
            password_hash=hash_password(data.password),
            invitation_token=None,
            joined_at=datetime.now(timezone.utc),
        )
        self._db.add(member)
        await self._db.flush()  # génère l'UUID sans commit

        # 5. Invalide le token (one-time use)
        await cache_delete(CacheKeys.invitation_token(data.token))

        # 6. Génère les tokens DIRECTEMENT depuis l'objet en mémoire
        # (pas de requête BDD → pas de problème de visibilité pré-commit)
        tokens = await self._create_tokens_for(member)
        await self._log(member.id, AuditAction.CREATE, "member", member.id)

        return tokens

    # ─────────────────────────────────────────────────────────────────────────
    # INSCRIPTION DEPUIS INVITATION GROUPÉE
    # ─────────────────────────────────────────────────────────────────────────

    async def register_from_group(
        self,
        data: RegisterFromGroupRequest,
    ) -> TokenResponse:
        """
        Crée un compte depuis un lien groupé (réutilisable).
        Le token de groupe N'EST PAS supprimé après l'inscription.
        """
        cached_value = await cache_get(CacheKeys.group_invite_token(data.group_token))
        if not cached_value:
            raise InvalidTokenError(
                "Ce lien d'invitation groupée est invalide ou a expiré."
            )

        try:
            token_data = json.loads(cached_value)
        except (json.JSONDecodeError, TypeError):
            raise InvalidTokenError("Token de groupe corrompu.")

        try:
            role = Role(token_data.get("role", "member"))
        except ValueError:
            role = Role.MEMBER

        # Unicité téléphone
        existing_phone = await self._db.execute(
            select(Member).where(Member.phone_number == data.phone_number)
        )
        if existing_phone.scalar_one_or_none():
            raise ConflictError(
                f"Un compte avec le numéro {data.phone_number} existe déjà."
            )

        if data.email:
            existing_email = await self._db.execute(
                select(Member).where(Member.email == data.email)
            )
            if existing_email.scalar_one_or_none():
                raise ConflictError(
                    f"Un compte avec l'email {data.email} existe déjà."
                )

        member = Member(
            full_name=data.full_name,
            phone_number=data.phone_number,
            email=data.email,
            role=role,
            status=MemberStatus.ACTIVE,
            password_hash=hash_password(data.password),
            invitation_token=None,
            joined_at=datetime.now(timezone.utc),
        )
        self._db.add(member)
        await self._db.flush()

        # Token de groupe NON supprimé → réutilisable
        tokens = await self._create_tokens_for(member)
        await self._log(member.id, AuditAction.CREATE, "member", member.id)

        return tokens

    # ─────────────────────────────────────────────────────────────────────────
    # VALIDATION TOKEN DE GROUPE
    # ─────────────────────────────────────────────────────────────────────────

    async def validate_group_token(self, token: str) -> dict:
        """
        Vérifie qu'un token de groupe est valide et retourne ses infos.
        Appelé par GET /auth/group-invite/{token} avant l'affichage du formulaire.
        """
        cached_value = await cache_get(CacheKeys.group_invite_token(token))
        if not cached_value:
            return {
                "is_valid": False,
                "message": "Ce lien d'invitation est invalide ou a expiré.",
                "default_role": None,
                "label": None,
                "expires_at": None,
            }

        try:
            token_data = json.loads(cached_value)
        except (json.JSONDecodeError, TypeError):
            return {
                "is_valid": False,
                "message": "Token corrompu.",
                "default_role": None,
                "label": None,
                "expires_at": None,
            }

        return {
            "is_valid": True,
            "message": "Lien valide.",
            "default_role": token_data.get("role", "member"),
            "label": token_data.get("label"),
            "expires_at": token_data.get("expires_at"),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # CHANGEMENT DE MOT DE PASSE
    # ─────────────────────────────────────────────────────────────────────────

    async def change_password(
        self,
        member: Member,
        data: ChangePasswordRequest,
    ) -> None:
        if not verify_password(member.password_hash, data.current_password):
            raise UnauthorizedError("Mot de passe actuel incorrect")
        if verify_password(member.password_hash, data.new_password):
            raise BusinessRuleError(
                "Le nouveau mot de passe doit être différent de l'ancien"
            )
        member.password_hash = hash_password(data.new_password)
        await cache_delete(CacheKeys.refresh_token(str(member.id)))
        await self._log(member.id, AuditAction.UPDATE, "member", member.id)

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

    async def _create_tokens_for(self, member: Member) -> TokenResponse:
        """
        Génère access_token + refresh_token pour un membre
        et stocke le refresh_token dans Redis.
        Utilisé par login() ET par les méthodes d'inscription
        (pour éviter d'appeler login() avant commit).
        """
        access_token  = create_access_token(
            subject=str(member.id),
            extra_claims={"role": member.role, "name": member.full_name},
        )
        refresh_token = create_refresh_token(subject=str(member.id))

        ttl = settings.jwt_refresh_token_expire_days * 86_400
        await cache_set(
            key=CacheKeys.refresh_token(str(member.id)),
            value=refresh_token,
            ttl_seconds=ttl,
        )

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            member_id=str(member.id),
            full_name=member.full_name,
            role=member.role,
        )

    async def _log(
        self,
        member_id,
        action: AuditAction,
        entity_type: str,
        entity_id,
        ip_address: str | None = None,
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