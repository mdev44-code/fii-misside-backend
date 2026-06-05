from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.projects.schemas import CreateProjectRequest, UpdateProjectRequest
from app.domains.projects.service import ProjectService
from app.infrastructure.database.session import get_db
from app.infrastructure.security.permissions import require_manager
from app.shared.enums import ProjectStatus
from app.shared.response import success_response

router = APIRouter()


@router.get("")
async def list_projects(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
    status: ProjectStatus | None = Query(
        default=None,
        description=(
            "Filtrer par statut : draft, in_progress, completed, suspended, abandoned, cancelled"
        ),
    ),
):
    service = ProjectService(db)
    projects = await service.get_all_projects(status=status)

    return success_response(
        data=[p.model_dump() for p in projects],
        message=f"{len(projects)} projet(s) trouvé(s)",
    )


@router.post("", dependencies=[Depends(require_manager)])
async def create_project(
    data: CreateProjectRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = ProjectService(db)
    project = await service.create_project(data, created_by=current_member)

    return success_response(
        data=project.model_dump(),
        message=f"Projet '{project.title}' créé avec succès",
    )


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = ProjectService(db)
    project = await service.get_project_by_id(project_id)

    return success_response(data=project.model_dump())


@router.patch("/{project_id}", dependencies=[Depends(require_manager)])
async def update_project(
    project_id: str,
    data: UpdateProjectRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = ProjectService(db)
    project = await service.update_project(
        project_id=project_id,
        data=data,
        updated_by=current_member,
    )

    return success_response(
        data=project.model_dump(),
        message="Projet mis à jour avec succès",
    )


@router.delete("/{project_id}", dependencies=[Depends(require_manager)])
async def delete_project(
    project_id: str,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = ProjectService(db)
    await service.delete_project(
        project_id=project_id,
        deleted_by=current_member,
    )

    return success_response(message="Projet supprimé avec succès")
