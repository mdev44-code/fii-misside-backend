import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.projects.schemas import (
    CreateProjectRequest,
    ProjectResponse,
    UpdateProjectRequest,
)
from app.infrastructure.database.models import AuditLog, Member, Project, Transaction
from app.shared.enums import AuditAction, ProjectStatus, PROJECT_STATUS_TRANSITIONS
from app.shared.exceptions import BusinessRuleError, NotFoundError


# Labels lisibles pour les messages d'erreur
_STATUS_LABELS: dict[ProjectStatus, str] = {
    ProjectStatus.DRAFT: "Brouillon",
    ProjectStatus.IN_PROGRESS: "En cours",
    ProjectStatus.COMPLETED: "Terminé",
    ProjectStatus.SUSPENDED: "Suspendu",
    ProjectStatus.ABANDONED: "Abandonné",
    ProjectStatus.CANCELLED: "Annulé",
}


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

        current_status = ProjectStatus(project.status)

        # ── Validation de la transition de statut ──
        if data.status and data.status != current_status:
            self._validate_status_transition(current_status, data.status)

        # Vérifie la règle métier AVANT de modifier quoi que ce soit
        new_status = data.status or current_status
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
                f"y sont liées. Passez-le en statut 'cancelled' ou 'abandoned' à la place."
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

    def _validate_status_transition(
        self,
        current: ProjectStatus,
        target: ProjectStatus,
    ) -> None:
        allowed = PROJECT_STATUS_TRANSITIONS.get(current, [])

        if target not in allowed:
            current_label = _STATUS_LABELS.get(current, current.value)
            target_label = _STATUS_LABELS.get(target, target.value)

            if not allowed:
                raise BusinessRuleError(
                    f"Le projet est en statut '{current_label}' qui est un statut terminal. "
                    f"Aucune modification de statut n'est possible."
                )

            allowed_labels = ", ".join(
                f"'{_STATUS_LABELS.get(s, s.value)}'" for s in allowed
            )
            raise BusinessRuleError(
                f"Transition de statut invalide : '{current_label}' → '{target_label}'. "
                f"Depuis '{current_label}', les transitions possibles sont : {allowed_labels}."
            )

    async def _get_project_or_404(self, project_id: str) -> Project:
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
        result = await self._db.execute(
            select(Member.full_name).where(Member.id == member_id)
        )
        name = result.scalar_one_or_none()
        return name or ""