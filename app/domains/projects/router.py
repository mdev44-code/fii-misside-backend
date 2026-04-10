"""
projects/router.py — Endpoints HTTP pour la gestion des projets.

Contrôle d'accès par route :
  GET  /projects          → tous les membres (lire = transparent pour tous)
  GET  /projects/{id}     → tous les membres
  POST /projects          → manager + admin uniquement
  PATCH /projects/{id}    → manager + admin uniquement
  DELETE /projects/{id}   → manager + admin uniquement

Pourquoi tous les membres peuvent lire ?
  La transparence sur les projets est importante dans une association.
  Tout le monde doit savoir sur quoi l'argent est dépensé.
  Seule la modification est restreinte aux responsables.
"""

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
        description="Filtrer par statut : draft, in_progress, completed, cancelled",
    ),
):
    """
    Liste tous les projets de l'association.
    Accessible à tous les membres connectés.

    Paramètre optionnel :
      ?status=in_progress → uniquement les projets en cours
      ?status=draft       → uniquement les projets en phase d'idée
    """
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
    """
    Crée un nouveau projet.
    Réservé aux gestionnaires et administrateurs.

    Le projet est créé en statut "draft" par défaut.
    Le budget est optionnel à la création.
    """
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
    """
    Retourne les détails d'un projet par son ID.
    Accessible à tous les membres connectés.
    """
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
    """
    Modifie un projet existant (modification partielle).
    Réservé aux gestionnaires et administrateurs.

    Seuls les champs envoyés dans le body sont modifiés.
    Règle métier : impossible de passer en 'in_progress' sans budget défini.

    Exemples d'utilisation :
      Ajouter un budget     : {"budget_allocated": 500000}
      Démarrer le projet    : {"status": "in_progress"}
      Changer le titre      : {"title": "Nouveau titre"}
      Terminer le projet    : {"status": "completed"}
    """
    service = ProjectService(db)
    project = await service.update_project(
        project_id=project_id,
        data=data,
        updated_by=current_member,
    )

    return success_response(
        data=project.model_dump(),
        message=f"Projet '{project.title}' mis à jour",
    )


@router.delete("/{project_id}", dependencies=[Depends(require_manager)])
async def delete_project(
    project_id: str,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Supprime un projet.
    Réservé aux gestionnaires et administrateurs.

    La suppression est refusée si des transactions sont liées au projet.
    Dans ce cas, passer le projet en statut 'cancelled' à la place.
    """
    service = ProjectService(db)
    await service.delete_project(
        project_id=project_id,
        deleted_by=current_member,
    )

    return success_response(message="Projet supprimé avec succès")