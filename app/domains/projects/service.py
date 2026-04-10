"""
projects/service.py — Logique métier pour la gestion des projets.

Responsabilités :
  1. Créer un projet (avec ou sans budget)
  2. Lister tous les projets (avec filtre optionnel par statut)
  3. Récupérer un projet par son ID
  4. Modifier un projet (titre, description, budget, statut, dates)
  5. Supprimer un projet

Règles métier appliquées ici :
  - Passer en "in_progress" sans budget défini → BusinessRuleError
  - Supprimer un projet qui a des transactions liées → BusinessRuleError
    (on ne peut pas supprimer l'historique financier)
  - budget_spent ne peut pas être modifié manuellement
    (il est géré automatiquement par treasury/service.py)
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.projects.schemas import (
    CreateProjectRequest,
    ProjectResponse,
    UpdateProjectRequest,
)
from app.infrastructure.database.models import AuditLog, Member, Project, Transaction
from app.shared.enums import AuditAction, ProjectStatus
from app.shared.exceptions import BusinessRuleError, NotFoundError


class ProjectService:

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ─────────────────────────────────────────────────────────────────────────
    # CRÉER
    # ─────────────────────────────────────────────────────────────────────────

    async def create_project(
        self,
        data: CreateProjectRequest,
        created_by: Member,
    ) -> ProjectResponse:
        """
        Crée un nouveau projet.

        Le statut initial est toujours "draft" — on ne peut pas créer
        un projet directement en "in_progress" sans passer par une modification.
        Le créateur est enregistré pour l'audit et l'affichage.
        """
        project = Project(
            title=data.title,
            description=data.description,
            budget_allocated=data.budget_allocated,
            created_by=created_by.id,
            start_date=data.start_date,
            end_date=data.end_date,
            # statut toujours draft à la création
            status=ProjectStatus.DRAFT,
        )
        self._db.add(project)
        await self._db.flush()  # génère l'UUID sans committer

        self._db.add(AuditLog(
            member_id=created_by.id,
            action=AuditAction.CREATE,
            entity_type="project",
            entity_id=project.id,
            new_values={
                "title": project.title,
                "budget_allocated": data.budget_allocated,
            },
        ))

        return ProjectResponse.from_model(project, creator_name=created_by.full_name)

    # ─────────────────────────────────────────────────────────────────────────
    # LIRE
    # ─────────────────────────────────────────────────────────────────────────

    async def get_all_projects(
        self,
        status: ProjectStatus | None = None,
    ) -> list[ProjectResponse]:
        """
        Retourne tous les projets, triés par date de création décroissante.
        Filtre optionnel par statut : ?status=in_progress

        On charge le nom du créateur en une requête supplémentaire par projet.
        Pour ce volume (petite association), c'est acceptable.
        Si le volume grandit, on pourrait faire une jointure SQL.
        """
        stmt = select(Project)

        if status:
            stmt = stmt.where(Project.status == status)

        stmt = stmt.order_by(Project.created_at.desc())

        result = await self._db.execute(stmt)
        projects = result.scalars().all()

        responses = []
        for project in projects:
            creator_name = await self._get_member_name(project.created_by)
            responses.append(
                ProjectResponse.from_model(project, creator_name=creator_name)
            )

        return responses

    async def get_project_by_id(self, project_id: str) -> ProjectResponse:
        """
        Retourne un projet par son UUID.
        Lève NotFoundError si inexistant.
        """
        project = await self._get_project_or_404(project_id)
        creator_name = await self._get_member_name(project.created_by)
        return ProjectResponse.from_model(project, creator_name=creator_name)

    # ─────────────────────────────────────────────────────────────────────────
    # MODIFIER
    # ─────────────────────────────────────────────────────────────────────────

    async def update_project(
        self,
        project_id: str,
        data: UpdateProjectRequest,
        updated_by: Member,
    ) -> ProjectResponse:
        """
        Modifie un projet de façon partielle (PATCH).

        Seuls les champs fournis dans le body sont modifiés.
        model_dump(exclude_none=True) retourne uniquement les champs
        qui ont été explicitement envoyés — pas les None par défaut.

        Règle métier : passer en "in_progress" sans budget défini → erreur.
        Le message explique clairement ce qu'il faut faire.
        """
        project = await self._get_project_or_404(project_id)

        # Capture les anciennes valeurs pour l'audit
        old_values = {
            "title": project.title,
            "status": project.status,
            "budget_allocated": (
                float(project.budget_allocated)
                if project.budget_allocated is not None else None
            ),
        }

        # Vérifie la règle métier AVANT de modifier quoi que ce soit
        new_status = data.status or project.status
        new_budget = data.budget_allocated if data.budget_allocated is not None else project.budget_allocated

        if new_status == ProjectStatus.IN_PROGRESS and new_budget is None:
            raise BusinessRuleError(
                "Impossible de démarrer un projet sans budget défini. "
                "Ajoutez un budget avant de passer en 'in_progress'."
            )

        # Applique les modifications — exclude_none=True : ignore les champs
        # non fournis dans le body (on ne les remet pas à None)
        updates = data.model_dump(exclude_none=True)
        for field, value in updates.items():
            setattr(project, field, value)

        self._db.add(AuditLog(
            member_id=updated_by.id,
            action=AuditAction.UPDATE,
            entity_type="project",
            entity_id=project.id,
            old_values=old_values,
            new_values=updates,
        ))

        creator_name = await self._get_member_name(project.created_by)
        return ProjectResponse.from_model(project, creator_name=creator_name)

    # ─────────────────────────────────────────────────────────────────────────
    # SUPPRIMER
    # ─────────────────────────────────────────────────────────────────────────

    async def delete_project(
        self,
        project_id: str,
        deleted_by: Member,
    ) -> None:
        """
        Supprime un projet.

        Règle métier : on refuse la suppression si des transactions
        sont liées à ce projet. Supprimer un projet avec des dépenses
        enregistrées rendrait l'historique financier incohérent.

        Dans ce cas, on suggère de passer le projet en "cancelled"
        plutôt que de le supprimer.
        """
        project = await self._get_project_or_404(project_id)

        # Vérifie s'il y a des transactions liées
        tx_count_result = await self._db.execute(
            select(func.count(Transaction.id)).where(
                Transaction.project_id == project.id
            )
        )
        tx_count = tx_count_result.scalar_one()

        if tx_count > 0:
            raise BusinessRuleError(
                f"Impossible de supprimer ce projet : {tx_count} transaction(s) "
                f"y sont liées. Passez-le en statut 'cancelled' à la place."
            )

        self._db.add(AuditLog(
            member_id=deleted_by.id,
            action=AuditAction.DELETE,
            entity_type="project",
            entity_id=project.id,
            old_values={"title": project.title, "status": project.status},
        ))

        await self._db.delete(project)

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS PRIVÉS
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_project_or_404(self, project_id: str) -> Project:
        """
        Charge un projet par son UUID ou lève NotFoundError.
        Réutilisé dans get, update et delete.
        """
        try:
            project_uuid = uuid.UUID(project_id)
        except ValueError as exc:
            raise NotFoundError("Projet", project_id) from exc

        result = await self._db.execute(
            select(Project).where(Project.id == project_uuid)
        )
        project = result.scalar_one_or_none()

        if not project:
            raise NotFoundError("Projet", project_id)

        return project

    async def _get_member_name(self, member_id: uuid.UUID) -> str:
        """
        Retourne le nom complet d'un membre par son UUID.
        Retourne une chaîne vide si le membre est introuvable.
        """
        result = await self._db.execute(
            select(Member.full_name).where(Member.id == member_id)
        )
        name = result.scalar_one_or_none()
        return name or ""