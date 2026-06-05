import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from argon2 import hash_password, PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.members.schemas import (
    CreateGroupInviteRequest,
    GroupInviteResponse,
    GroupInviteValidationResponse,
    InviteMemberRequest,
    InviteMemberResponse,
    MemberResponse,
    OrgChartMemberResponse,
    OrgChartResponse,
    RegisterFromGroupInviteRequest,
    UpdateProfileRequest
)
from app.infrastructure.cache.redis import CacheKeys, cache_set
from app.infrastructure.database.models import AuditLog, GroupInvitation, Member
from app.shared.enums import AuditAction, MemberStatus, Role
from app.shared.exceptions import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.infrastructure.storage.s3 import delete_profile_picture, upload_profile_picture

from app.domains.auth.schemas import TokenResponse

from app.infrastructure.security.jwt import create_access_token, create_refresh_token

_hasher = PasswordHasher()
class MemberService:

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ─────────────────────────────────────────────────────────────────────────
    # INVITATION
    # ─────────────────────────────────────────────────────────────────────────

    async def invite_member(
        self,
        data: InviteMemberRequest,
        invited_by: Member,
    ) -> InviteMemberResponse:
        # Génère un token sécurisé
        token = secrets.token_urlsafe(32)
 
        # Stocke le rôle dans Redis avec TTL
        payload = json.dumps({"role": data.role.value, "type": "personal"})
        ttl = settings.invitation_token_expire_hours * 3600
        await cache_set(
            key=CacheKeys.invitation_token(token),
            value=payload,
            ttl_seconds=ttl,
        )
 
        # Audit log — trace qui a généré l'invitation
        await self._log(
            invited_by.id, AuditAction.CREATE, "invitation", invited_by.id
        )
 
        invitation_link = f"{settings.frontend_url}/register?token={token}"
 
        return InviteMemberResponse(
            invitation_link=invitation_link,
            message=(
                f"Lien d'invitation généré pour le rôle « {data.role.value} ». "
                f"Partagez-le par WhatsApp ou SMS. "
                f"Il expire dans {settings.invitation_token_expire_hours}h "
                f"et ne peut être utilisé qu'une seule fois."
            ),
        )
 
    # ─────────────────────────────────────────────────────────────────────────
    # LECTURE
    # ─────────────────────────────────────────────────────────────────────────
 
    async def get_all_members(self) -> list[MemberResponse]:
        result = await self._db.execute(
            select(Member).where(Member.status == "active").order_by(Member.full_name)
        )
        members = result.scalars().all()
        return [MemberResponse.from_model(m) for m in members]
 
    async def get_member_by_id(self, member_id: str) -> MemberResponse:
        result = await self._db.execute(
            select(Member).where(Member.id == uuid.UUID(member_id))
        )
        member = result.scalar_one_or_none()
 
        if not member:
            raise NotFoundError("Membre", member_id)
 
        return MemberResponse.from_model(member)
 
    async def get_org_chart(self) -> OrgChartResponse:
        result = await self._db.execute(
            select(Member)
            .where(Member.status == MemberStatus.ACTIVE)
            .order_by(Member.full_name)
        )
        members = result.scalars().all()
 
        groups: dict[str, list[OrgChartMemberResponse]] = {
            Role.ADMIN: [],
            Role.TREASURER: [],
            Role.MANAGER: [],
            Role.MEMBER: [],
        }
 
        for m in members:
            if m.role in groups:
                groups[m.role].append(OrgChartMemberResponse.from_model(m))
 
        return OrgChartResponse(
            admin=groups[Role.ADMIN],
            treasurer=groups[Role.TREASURER],
            manager=groups[Role.MANAGER],
            member=groups[Role.MEMBER],
        )
 
    # ─────────────────────────────────────────────────────────────────────────
    # MODIFICATION
    # ─────────────────────────────────────────────────────────────────────────
 
    async def update_role(
        self,
        member_id: str,
        new_role: Role,
        updated_by: Member,
    ) -> MemberResponse:
        """Change le rôle d'un membre."""
        if str(updated_by.id) == member_id:
            raise BusinessRuleError("Vous ne pouvez pas modifier votre propre rôle.")
 
        member = await self._get_member_or_404(member_id)
 
        # Protège le dernier admin actif
        if member.role == Role.ADMIN and new_role != Role.ADMIN:
            active_admins = await self._count_active_admins()
            if active_admins <= 1:
                raise BusinessRuleError(
                    "Impossible de retirer le rôle admin : "
                    "il doit rester au moins un administrateur actif."
                )
 
        old_role = member.role
        member.role = new_role
        await self._db.flush()
 
        await self._log_with_diff(
            updated_by.id, AuditAction.UPDATE, "member", member.id,
            old_values={"role": old_role},
            new_values={"role": new_role},
        )
        return MemberResponse.from_model(member)
 
    async def update_status(
        self,
        member_id: str,
        new_status: str,
        updated_by: Member,
    ) -> MemberResponse:
        """Change le statut d'un membre (active/inactive/suspended)."""
        member = await self._get_member_or_404(member_id)
        old_status = member.status
        member.status = new_status
        await self._db.flush()
 
        await self._log_with_diff(
            updated_by.id, AuditAction.UPDATE, "member", member.id,
            old_values={"status": old_status},
            new_values={"status": new_status},
        )
        return MemberResponse.from_model(member)
 
    async def delete_member(
        self,
        member_id: str,
        deleted_by: Member,
    ) -> None:
        member = await self._get_member_or_404(member_id)
 
        # Protection : impossible de se supprimer soi-même
        if str(member.id) == str(deleted_by.id):
            raise BusinessRuleError(
                "Vous ne pouvez pas supprimer votre propre compte"
            )
 
        # Protection : impossible de supprimer le seul admin actif
        if member.role == Role.ADMIN:
            admin_count = await self._count_active_admins()
            if admin_count <= 1:
                raise BusinessRuleError(
                    "Impossible de supprimer le seul administrateur actif"
                )
 
        # Audit avant suppression (car l'entité sera effacée)
        self._db.add(AuditLog(
            member_id=deleted_by.id,
            action=AuditAction.DELETE,
            entity_type="member",
            entity_id=member.id,
            old_values={
                "full_name": member.full_name,
                "phone_number": member.phone_number,
                "role": str(member.role),
                "status": str(member.status),
            },
        ))
 
        # Supprime la photo de profil S3 si elle existe
        if member.profile_picture_url:
            try:
                await delete_profile_picture(member.profile_picture_url)
            except Exception:
                pass  # On ne bloque pas la suppression si S3 échoue
 
        await self._db.delete(member)
 

    async def get_me(self, member: Member) -> MemberResponse:
        """Retourne le profil du membre connecté."""
        return MemberResponse.from_model(member)
 
    async def update_profile(
        self,
        member: Member,
        data: UpdateProfileRequest,
    ) -> MemberResponse:
        """
        Modifie le profil du membre connecté.
        Seuls les champs envoyés sont mis à jour (pattern PATCH).
        """
        if data.full_name is not None:
            member.full_name = data.full_name
 
        if data.email is not None:
            existing = await self._db.execute(
                select(Member).where(
                    Member.email == data.email,
                    Member.id != member.id,
                )
            )
            if existing.scalar_one_or_none():
                raise ConflictError("Cet email est déjà utilisé par un autre membre.")
            member.email = data.email
 
        if data.phone_number is not None:
            existing = await self._db.execute(
                select(Member).where(
                    Member.phone_number == data.phone_number,
                    Member.id != member.id,
                )
            )
            if existing.scalar_one_or_none():
                raise ConflictError("Ce numéro est déjà utilisé par un autre membre.")
            member.phone_number = data.phone_number
 
        await self._db.flush()
        await self._log(member.id, AuditAction.UPDATE, "member", member.id)
        return MemberResponse.from_model(member)
 
    async def upload_avatar(
        self,
        member: Member,
        file_content: bytes,
        content_type: str,
        filename: str,
    ) -> MemberResponse:
        """Upload la photo de profil vers S3."""
        if member.profile_picture_url:
            await delete_profile_picture(member.profile_picture_url)
 
        url = await upload_profile_picture(
            file_content=file_content,
            content_type=content_type,
            filename=filename,
            member_id=str(member.id),
        )
        member.profile_picture_url = url
        await self._db.flush()
        return MemberResponse.from_model(member)
 
    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS PRIVÉS
    # ─────────────────────────────────────────────────────────────────────────
 
    async def _get_member_or_404(self, member_id: str) -> Member:
        """Charge un membre par son UUID ou lève NotFoundError."""
        result = await self._db.execute(
            select(Member).where(Member.id == uuid.UUID(member_id))
        )
        member = result.scalar_one_or_none()
        if not member:
            raise NotFoundError("Membre", member_id)
        return member
 
    async def _count_active_admins(self) -> int:
        """Compte le nombre d'admins actifs."""
        from sqlalchemy import func
        result = await self._db.execute(
            select(func.count(Member.id)).where(
                Member.role == Role.ADMIN,
                Member.status == MemberStatus.ACTIVE,
            )
        )
        return result.scalar_one()
 
    async def _log(
        self,
        member_id,
        action: AuditAction,
        entity_type: str,
        entity_id,
    ) -> None:
        """Enregistre une action simple dans les audit_logs."""
        log = AuditLog(
            member_id=member_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        self._db.add(log)
 
    async def _log_with_diff(
        self,
        member_id,
        action: AuditAction,
        entity_type: str,
        entity_id,
        old_values: dict,
        new_values: dict,
    ) -> None:
        """Enregistre une action avec les valeurs avant/après."""
        log = AuditLog(
            member_id=member_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=old_values,
            new_values=new_values,
        )
        self._db.add(log)
 
    
    # ─────────────────────────────────────────────────────────────────────────
    # CRÉATION DU LIEN
    # ─────────────────────────────────────────────────────────────────────────
 
    async def create_group_invite(
        self,
        data: CreateGroupInviteRequest,
        created_by: Member,
    ) -> GroupInviteResponse:
        # 1. Valider le rôle
        valid_roles = {r.value for r in Role}
        if data.default_role not in valid_roles:
            raise BusinessRuleError(
                f"Rôle invalide : '{data.default_role}'. "
                f"Valeurs acceptées : {', '.join(valid_roles)}"
            )
 
        # 2. Générer un token unique (48 bytes → 64 chars URL-safe)
        # Plus long que le token personnel (32 bytes) pour plus de sécurité
        # car il sera partagé publiquement dans des groupes
        token = secrets.token_urlsafe(48)
 
        # 3. Calcul de la date d'expiration
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=data.expires_in_hours
        )
 
        # 4. Créer en BDD
        invite = GroupInvitation(
            token=token,
            created_by=created_by.id,
            default_role=data.default_role,
            label=data.label,
            expires_at=expires_at,
            max_uses=data.max_uses,
            use_count=0,
            is_active=True,
        )
        self._db.add(invite)
        await self._db.flush()
 
        return GroupInviteResponse.from_model(invite, settings.frontend_url)
 
    # ─────────────────────────────────────────────────────────────────────────
    # LISTE DES LIENS
    # ─────────────────────────────────────────────────────────────────────────
 
    async def list_group_invites(
        self,
        created_by: Member,
        active_only: bool = True,
    ) -> list[GroupInviteResponse]:
        query = select(GroupInvitation).where(
            GroupInvitation.created_by == created_by.id
        )
 
        if active_only:
            now = datetime.now(timezone.utc)
            query = query.where(
                GroupInvitation.is_active == True,  # noqa: E712
                GroupInvitation.expires_at > now,
            )
 
        query = query.order_by(GroupInvitation.created_at.desc())
        result = await self._db.execute(query)
        invites = result.scalars().all()
 
        return [
            GroupInviteResponse.from_model(inv, settings.frontend_url)
            for inv in invites
        ]
 
    # ─────────────────────────────────────────────────────────────────────────
    # SUPPRESSION & DÉSACTIVATION
    # ─────────────────────────────────────────────────────────────────────────
 

    async def delete_group_invite(
        self,
        invite_id: str,
        requested_by: Member,
    ) -> None:
        """
        Supprime définitivement un lien d'invitation groupé.
 
        Différence avec deactivate :
          - deactivate → is_active = False (réversible, audit trail conservé)
          - delete     → suppression physique en BDD (irréversible)
 
        Seul le créateur du lien ou un admin peut le supprimer.
        """
        import uuid as _uuid
        result = await self._db.execute(
            select(GroupInvitation).where(
                GroupInvitation.id == _uuid.UUID(invite_id)
            )
        )
        invite = result.scalar_one_or_none()
 
        if not invite:
            raise NotFoundError("Lien d'invitation")
 
        # Seul le créateur ou un admin peut supprimer
        if str(invite.created_by) != str(requested_by.id):
            if requested_by.role != Role.ADMIN:
                raise ForbiddenError(
                    "Vous ne pouvez supprimer que vos propres liens d'invitation"
                )
 
        await self._db.delete(invite)
        await self._db.flush()


    async def deactivate_group_invite(
        self,
        invite_id: str,
        requested_by: Member,
    ) -> GroupInviteResponse:
        """
        Désactive un lien d'invitation groupé (sans le supprimer).
 
        Seul l'admin qui a créé le lien peut le désactiver,
        sauf si le demandeur est lui-même admin (superadmin pattern).
 
        On désactive au lieu de supprimer pour conserver l'audit trail :
        savoir combien de personnes ont utilisé ce lien avant sa désactivation.
        """
        import uuid as _uuid
        result = await self._db.execute(
            select(GroupInvitation).where(
                GroupInvitation.id == _uuid.UUID(invite_id)
            )
        )
        invite = result.scalar_one_or_none()
 
        if not invite:
            raise NotFoundError("Lien d'invitation")
 
        # Seul le créateur ou un autre admin peut désactiver
        if str(invite.created_by) != str(requested_by.id):
            if requested_by.role != Role.ADMIN:
                raise ForbiddenError(
                    "Vous ne pouvez désactiver que vos propres liens d'invitation"
                )
 
        invite.is_active = False
        await self._db.flush()
 
        return GroupInviteResponse.from_model(invite, settings.frontend_url)
 
    # ─────────────────────────────────────────────────────────────────────────
    # VALIDATION DU TOKEN (route publique)
    # ─────────────────────────────────────────────────────────────────────────
 
    async def validate_group_token(self, token: str) -> GroupInviteValidationResponse:
        """
        Vérifie si un token de groupe est valide.
 
        Appelé par le frontend AVANT d'afficher le formulaire d'inscription
        pour éviter de laisser l'utilisateur remplir un long formulaire
        puis se faire rejeter.
 
        Un token est valide si :
          1. Il existe en BDD
          2. is_active = True
          3. expires_at > maintenant
          4. use_count < max_uses (si max_uses défini)
        """
        result = await self._db.execute(
            select(GroupInvitation).where(GroupInvitation.token == token)
        )
        invite = result.scalar_one_or_none()
 
        if not invite:
            return GroupInviteValidationResponse(
                is_valid=False,
                default_role="member",
                label=None,
                expires_at="",
                message="Ce lien d'invitation est invalide ou n'existe pas.",
            )
 
        now = datetime.now(timezone.utc)
 
        # Vérifier expiration
        if invite.expires_at < now:
            return GroupInviteValidationResponse(
                is_valid=False,
                default_role=invite.default_role,
                label=invite.label,
                expires_at=invite.expires_at.isoformat(),
                message="Ce lien d'invitation a expiré. Demandez un nouveau lien à l'administrateur.",
            )
 
        # Vérifier désactivation manuelle
        if not invite.is_active:
            return GroupInviteValidationResponse(
                is_valid=False,
                default_role=invite.default_role,
                label=invite.label,
                expires_at=invite.expires_at.isoformat(),
                message="Ce lien d'invitation a été désactivé par l'administrateur.",
            )
 
        # Vérifier quota
        if invite.max_uses is not None and invite.use_count >= invite.max_uses:
            return GroupInviteValidationResponse(
                is_valid=False,
                default_role=invite.default_role,
                label=invite.label,
                expires_at=invite.expires_at.isoformat(),
                message=f"Ce lien d'invitation a atteint son nombre maximum d'utilisations ({invite.max_uses}).",
            )
 
        return GroupInviteValidationResponse(
            is_valid=True,
            default_role=invite.default_role,
            label=invite.label,
            expires_at=invite.expires_at.isoformat(),
            message="Lien valide. Vous pouvez créer votre compte.",
        )
 
    # ─────────────────────────────────────────────────────────────────────────
    # INSCRIPTION VIA LIEN GROUPÉ
    # ─────────────────────────────────────────────────────────────────────────
 
    async def register_from_group_invite(
        self,
        data: RegisterFromGroupInviteRequest,
    ) -> TokenResponse:
        # 1. Récupérer et valider l'invitation
        result = await self._db.execute(
            select(GroupInvitation).where(GroupInvitation.token == data.group_token)
        )
        invite = result.scalar_one_or_none()
 
        if not invite:
            raise BusinessRuleError(
                "Ce lien d'invitation est invalide ou n'existe pas. "
                "Demandez un nouveau lien à l'administrateur."
            )
 
        now = datetime.now(timezone.utc)
 
        if invite.expires_at < now:
            raise BusinessRuleError(
                "Ce lien d'invitation a expiré. "
                "Demandez un nouveau lien à l'administrateur."
            )
 
        if not invite.is_active:
            raise BusinessRuleError(
                "Ce lien d'invitation a été désactivé par l'administrateur."
            )
 
        if invite.max_uses is not None and invite.use_count >= invite.max_uses:
            raise BusinessRuleError(
                f"Ce lien d'invitation a atteint son nombre maximum "
                f"d'utilisations ({invite.max_uses})."
            )
 
        # 2. Vérifier unicité téléphone
        existing_phone = await self._db.execute(
            select(Member).where(Member.phone_number == data.phone_number)
        )
        if existing_phone.scalar_one_or_none():
            raise ConflictError(
                f"Un compte avec le numéro {data.phone_number} existe déjà. "
                "Connectez-vous ou utilisez un autre numéro."
            )
 
        # Vérifier unicité email si fourni
        if data.email:
            existing_email = await self._db.execute(
                select(Member).where(Member.email == data.email)
            )
            if existing_email.scalar_one_or_none():
                raise ConflictError(
                    f"Un compte avec l'email {data.email} existe déjà. "
                    "Connectez-vous ou utilisez un autre email."
                )
 
        # 3. Créer le membre directement ACTIVE
        # Pas de PENDING ici : la personne remplit tout d'un coup
        member = Member(
            full_name=data.full_name,
            phone_number=data.phone_number,
            email=data.email,
            password_hash=_hasher.hash(data.password),
            role=invite.default_role,
            status=MemberStatus.ACTIVE,
            joined_at=now,
            # Pas d'invitation_token : ce membre vient d'un lien de groupe
            invitation_token=None,
            invited_at=None,
        )
        self._db.add(member)
        await self._db.flush()
 
        # 4. Incrémenter le compteur d'utilisations du lien
        invite.use_count += 1
        await self._db.flush()
 
        # 5. Générer les tokens JWT
        access_token = create_access_token(
            subject=str(member.id),
            extra_claims={"role": member.role, "name": member.full_name},
        )
        refresh_token = create_refresh_token(subject=str(member.id))
 
        # Stocker le refresh token dans Redis
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