"""
members/service.py — Logique métier pour la gestion des membres.

Responsabilités :
  1. Inviter un nouveau membre (génération du token + lien)
  2. Lister les membres et construire l'organigramme
  3. Modifier le rôle ou le statut d'un membre
  4. Désactiver un membre

Pattern utilisé : Repository léger intégré au service.
Pour ce projet de taille moyenne, on n'a pas besoin d'une couche Repository
séparée — les requêtes SQLAlchemy restent dans le service.
"""

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.members.schemas import (
    InviteMemberRequest,
    InviteMemberResponse,
    MemberResponse,
    OrgChartMemberResponse,
    OrgChartResponse,
)
from app.infrastructure.cache.redis import CacheKeys, cache_set
from app.infrastructure.database.models import AuditLog, Member
from app.shared.enums import AuditAction, MemberStatus, Role
from app.shared.exceptions import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)


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
        """
        Crée une invitation pour un nouveau membre.

        Étapes :
          1. Vérifie qu'aucun membre n'a déjà ce numéro (unicité)
          2. Génère un token sécurisé (32 bytes → 43 chars URL-safe)
          3. Crée le membre en BDD avec statut PENDING
          4. Stocke le token dans Redis avec TTL (72h par défaut)
          5. Construit le lien d'invitation à partager

        secrets.token_urlsafe() : génère un token aléatoire et sécurisé
        utilisable dans une URL (pas de caractères spéciaux problématiques).
        Exemple : "Dq3mK9vL2nXpRtYz..."
        """
        # 1. Vérifie l'unicité du numéro de téléphone
        existing = await self._db.execute(
            select(Member).where(Member.phone_number == data.phone_number)
        )
        if existing.scalar_one_or_none():
            raise ConflictError(
                f"Un membre avec le numéro {data.phone_number} existe déjà"
            )

        # Vérifie aussi l'email si fourni
        if data.email:
            existing_email = await self._db.execute(
                select(Member).where(Member.email == data.email)
            )
            if existing_email.scalar_one_or_none():
                raise ConflictError(
                    f"Un membre avec l'email {data.email} existe déjà"
                )

        # 2. Génère un token unique et sécurisé
        token = secrets.token_urlsafe(32)

        # 3. Crée le membre en BDD
        member = Member(
            full_name=data.full_name,
            phone_number=data.phone_number,
            email=data.email,
            role=data.role,
            status=MemberStatus.PENDING,
            invitation_token=token,
            invited_at=datetime.now(timezone.utc),
        )
        self._db.add(member)

        # flush() : PostgreSQL génère l'UUID et le retourne en Python
        # sans encore committer → on peut utiliser member.id juste après
        await self._db.flush()

        # 4. Stocke le token dans Redis avec TTL
        # On associe le token à l'id du membre pour la double vérification
        # lors de l'inscription (voir auth/service.py register_from_invite)
        ttl = settings.invitation_token_expire_hours * 3600
        await cache_set(
            key=CacheKeys.invitation_token(token),
            value=str(member.id),
            ttl_seconds=ttl,
        )

        # 5. Audit log
        await self._log(
            invited_by.id, AuditAction.CREATE, "member", member.id
        )

        # 6. Construit la réponse avec le lien d'invitation
        invitation_link = (
            f"{settings.frontend_url}/register?token={token}"
        )

        return InviteMemberResponse(
            member=MemberResponse.from_model(member),
            invitation_link=invitation_link,
            message=(
                f"Invitation créée pour {member.full_name}. "
                f"Partagez ce lien par WhatsApp ou SMS. "
                f"Il expire dans {settings.invitation_token_expire_hours}h."
            ),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # LECTURE
    # ─────────────────────────────────────────────────────────────────────────

    async def get_all_members(self) -> list[MemberResponse]:
        """
        Retourne tous les membres triés par nom.

        scalars() : extrait les objets Member de la liste de tuples
                    que SQLAlchemy retourne par défaut.
        all()     : consomme le générateur et retourne une liste Python.
        """
        result = await self._db.execute(
            select(Member).order_by(Member.full_name)
        )
        members = result.scalars().all()
        return [MemberResponse.from_model(m) for m in members]

    async def get_member_by_id(self, member_id: str) -> MemberResponse:
        """
        Retourne un membre par son UUID.

        uuid.UUID(member_id) : convertit la string en objet UUID Python
        pour que SQLAlchemy puisse faire la comparaison correctement.
        """
        result = await self._db.execute(
            select(Member).where(Member.id == uuid.UUID(member_id))
        )
        member = result.scalar_one_or_none()

        if not member:
            raise NotFoundError("Membre", member_id)

        return MemberResponse.from_model(member)

    async def get_org_chart(self) -> OrgChartResponse:
        """
        Retourne l'organigramme groupé par rôle.

        On charge uniquement les membres ACTIFS — les membres PENDING
        (invitation non acceptée) n'apparaissent pas dans l'organigramme.

        Algorithme :
          1. Charge tous les membres actifs
          2. Les répartit dans des listes selon leur rôle
          3. Construit l'OrgChartResponse
        """
        result = await self._db.execute(
            select(Member)
            .where(Member.status == MemberStatus.ACTIVE)
            .order_by(Member.full_name)
        )
        members = result.scalars().all()

        # Initialise les 4 groupes vides
        groups: dict[str, list[OrgChartMemberResponse]] = {
            Role.ADMIN: [],
            Role.TREASURER: [],
            Role.MANAGER: [],
            Role.MEMBER: [],
        }

        # Répartit chaque membre dans son groupe
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
        """
        Change le rôle d'un membre.

        Règles métier :
          - On ne peut pas changer le rôle du seul admin (protection)
          - Un admin ne peut pas changer son propre rôle (sécurité)
        """
        member = await self._get_member_or_404(member_id)

        # Protection : impossible de se retirer son propre rôle admin
        if str(member.id) == str(updated_by.id):
            raise BusinessRuleError(
                "Vous ne pouvez pas modifier votre propre rôle"
            )

        # Protection : si c'est le seul admin, on ne peut pas lui retirer
        if member.role == Role.ADMIN and new_role != Role.ADMIN:
            admin_count = await self._count_admins()
            if admin_count <= 1:
                raise BusinessRuleError(
                    "Impossible : il doit toujours rester au moins un administrateur"
                )

        old_role = member.role
        member.role = new_role

        # Audit avec les valeurs avant/après
        await self._log_with_diff(
            updated_by.id,
            AuditAction.UPDATE,
            "member",
            member.id,
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
        """
        Change le statut d'un membre (active/inactive/suspended).

        Règle métier : on ne peut pas désactiver le seul admin.
        """
        member = await self._get_member_or_404(member_id)

        if str(member.id) == str(updated_by.id):
            raise BusinessRuleError(
                "Vous ne pouvez pas modifier votre propre statut"
            )

        if member.role == Role.ADMIN and new_status != MemberStatus.ACTIVE:
            admin_count = await self._count_active_admins()
            if admin_count <= 1:
                raise BusinessRuleError(
                    "Impossible de désactiver le seul administrateur actif"
                )

        old_status = member.status
        member.status = new_status

        await self._log_with_diff(
            updated_by.id,
            AuditAction.UPDATE,
            "member",
            member.id,
            old_values={"status": old_status},
            new_values={"status": new_status},
        )

        return MemberResponse.from_model(member)

    # ─────────────────────────────────────────────────────────────────────────
    # MÉTHODES PRIVÉES (helpers internes)
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_member_or_404(self, member_id: str) -> Member:
        """
        Charge un membre par son ID ou lève NotFoundError.
        Méthode privée (préfixe _) réutilisée dans plusieurs méthodes publiques.
        """
        result = await self._db.execute(
            select(Member).where(Member.id == uuid.UUID(member_id))
        )
        member = result.scalar_one_or_none()
        if not member:
            raise NotFoundError("Membre", member_id)
        return member

    async def _count_admins(self) -> int:
        """Compte le nombre total d'admins (peu importe leur statut)."""
        from sqlalchemy import func
        result = await self._db.execute(
            select(func.count(Member.id)).where(Member.role == Role.ADMIN)
        )
        return result.scalar_one()

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
        """
        Enregistre une action avec les valeurs avant et après.
        Utile pour les modifications de rôle ou de statut :
        on peut savoir exactement ce qui a changé.
        """
        log = AuditLog(
            member_id=member_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=old_values,
            new_values=new_values,
        )
        self._db.add(log)