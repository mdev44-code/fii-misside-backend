import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.postes.schemas import (
    AssignPosteRequest,
    CreatePosteRequest,
    OrgChartPostesResponse,
    PosteResponse,
    UpdatePosteRequest,
)
from app.infrastructure.database.models import Member, Poste
from app.shared.enums import MemberStatus
from app.shared.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
)


class PosteService:

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ─────────────────────────────────────────────────────────────────────────
    # CRÉATION
    # ─────────────────────────────────────────────────────────────────────────

    async def create_poste(self, data: CreatePosteRequest) -> PosteResponse:
        """
        Crée un nouveau poste dans l'organigramme.

        Étapes :
          1. Vérifie qu'aucun poste n'a déjà ce titre
          2. Si un member_id est fourni, vérifie que le membre existe et est libre
          3. Crée le poste en BDD
        """
        # 1. Unicité du titre
        existing = await self._db.execute(
            select(Poste).where(Poste.title.ilike(data.title))
        )
        if existing.scalar_one_or_none():
            raise ConflictError(f"Un poste avec le titre '{data.title}' existe déjà")

        # 2. Vérification du membre si fourni
        member_uuid = None
        if data.member_id:
            member_uuid = uuid.UUID(data.member_id)
            await self._validate_member_available(member_uuid)

        # 3. Création
        poste = Poste(
            title=data.title,
            member_id=member_uuid,
        )
        self._db.add(poste)
        await self._db.flush()
        await self._db.refresh(poste)

        return PosteResponse.from_model(poste)

    # ─────────────────────────────────────────────────────────────────────────
    # LECTURE
    # ─────────────────────────────────────────────────────────────────────────

    async def get_all_postes(self) -> OrgChartPostesResponse:
        """
        Retourne l'organigramme complet : tous les postes avec leurs titulaires.
        Triés par titre alphabétique.
        """
        result = await self._db.execute(
            select(Poste).order_by(Poste.title)
        )
        postes = result.scalars().all()

        poste_list = [PosteResponse.from_model(p) for p in postes]
        occupied = sum(1 for p in poste_list if not p.is_vacant)
        vacant = sum(1 for p in poste_list if p.is_vacant)

        return OrgChartPostesResponse(
            postes=poste_list,
            total=len(poste_list),
            occupied=occupied,
            vacant=vacant,
        )

    async def get_poste_by_id(self, poste_id: str) -> PosteResponse:
        """Retourne un poste par son UUID."""
        poste = await self._find_poste(poste_id)
        return PosteResponse.from_model(poste)

    # ─────────────────────────────────────────────────────────────────────────
    # ATTRIBUTION / LIBÉRATION / REMPLACEMENT
    # ─────────────────────────────────────────────────────────────────────────

    async def assign_member(
        self,
        poste_id: str,
        data: AssignPosteRequest,
    ) -> PosteResponse:
        """
        Attribue un membre à un poste, libère le poste, ou remplace le titulaire.

        Cas 1 — member_id à null :
          → Libère le poste (le rend vacant)

        Cas 2 — member_id fourni, poste vacant :
          → Vérifie que le membre est disponible puis l'affecte

        Cas 3 — member_id fourni, poste déjà occupé (remplacement) :
          → L'ancien titulaire est automatiquement retiré
          → Le nouveau membre prend le poste

        Dans tous les cas :
          - Le nouveau membre doit être actif
          - Le nouveau membre ne doit pas déjà occuper un autre poste
        """
        poste = await self._find_poste(poste_id)

        if data.member_id is None:
            # Libérer le poste
            poste.member_id = None
        else:
            member_uuid = uuid.UUID(data.member_id)

            # Vérifie que le membre existe, est actif, et n'occupe pas un AUTRE poste
            await self._validate_member_available(
                member_uuid, exclude_poste_id=poste.id
            )

            # Remplacement : pas besoin de libérer explicitement l'ancien,
            # on écrase directement le member_id (l'ancien perd son poste)
            poste.member_id = member_uuid

        await self._db.flush()
        await self._db.refresh(poste)

        return PosteResponse.from_model(poste)

    # ─────────────────────────────────────────────────────────────────────────
    # MODIFICATION
    # ─────────────────────────────────────────────────────────────────────────

    async def update_poste(
        self,
        poste_id: str,
        data: UpdatePosteRequest,
    ) -> PosteResponse:
        """
        Modifie le titre d'un poste.
        Pattern PATCH : seuls les champs présents sont modifiés.
        """
        poste = await self._find_poste(poste_id)

        if data.title is not None and data.title.lower() != poste.title.lower():
            # Vérifie l'unicité du nouveau titre
            existing = await self._db.execute(
                select(Poste).where(
                    Poste.title.ilike(data.title),
                    Poste.id != poste.id,
                )
            )
            if existing.scalar_one_or_none():
                raise ConflictError(
                    f"Un poste avec le titre '{data.title}' existe déjà"
                )
            poste.title = data.title

        await self._db.flush()
        await self._db.refresh(poste)

        return PosteResponse.from_model(poste)

    # ─────────────────────────────────────────────────────────────────────────
    # SUPPRESSION
    # ─────────────────────────────────────────────────────────────────────────

    async def delete_poste(self, poste_id: str) -> None:
        """
        Supprime un poste de l'organigramme.
        Le membre affecté n'est PAS supprimé — il perd juste son poste.
        """
        poste = await self._find_poste(poste_id)
        await self._db.delete(poste)
        await self._db.flush()

    # ─────────────────────────────────────────────────────────────────────────
    # MÉTHODES PRIVÉES
    # ─────────────────────────────────────────────────────────────────────────

    async def _find_poste(self, poste_id: str) -> Poste:
        """Charge un poste par ID ou lève NotFoundError."""
        result = await self._db.execute(
            select(Poste).where(Poste.id == uuid.UUID(poste_id))
        )
        poste = result.scalar_one_or_none()
        if not poste:
            raise NotFoundError("Poste", poste_id)
        return poste

    async def _validate_member_available(
        self,
        member_id: uuid.UUID,
        exclude_poste_id: uuid.UUID | None = None,
    ) -> None:
        """
        Vérifie qu'un membre :
          1. Existe en BDD
          2. Est actif
          3. N'occupe pas déjà un AUTRE poste

        exclude_poste_id : exclut le poste en cours de modification
        (permet de réassigner au même poste sans erreur, et permet
        le remplacement sur un poste donné).
        """
        # 1. Le membre existe ?
        result = await self._db.execute(
            select(Member).where(Member.id == member_id)
        )
        member = result.scalar_one_or_none()
        if not member:
            raise NotFoundError("Membre", str(member_id))

        # 2. Le membre est actif ?
        if member.status != MemberStatus.ACTIVE:
            raise BusinessRuleError(
                f"Le membre '{member.full_name}' n'est pas actif "
                f"(statut : {member.status}). Seuls les membres actifs "
                f"peuvent occuper un poste."
            )

        # 3. Le membre n'occupe pas déjà un AUTRE poste ?
        query = select(Poste).where(Poste.member_id == member_id)
        if exclude_poste_id:
            query = query.where(Poste.id != exclude_poste_id)

        result = await self._db.execute(query)
        existing_poste = result.scalar_one_or_none()
        if existing_poste:
            raise ConflictError(
                f"Le membre '{member.full_name}' occupe déjà le poste "
                f"'{existing_poste.title}'. Un membre ne peut occuper "
                f"qu'un seul poste à la fois."
            )