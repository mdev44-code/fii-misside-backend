"""
projects/schemas.py — Forme des données pour le domaine projets.

Qui peut faire quoi :
  - Lire les projets     : tous les membres connectés
  - Créer un projet      : manager + admin
  - Modifier un projet   : manager + admin
  - Supprimer un projet  : manager + admin

Règle métier rappel :
  budget_allocated est nullable — un projet peut être créé
  sans budget défini (phase idée). Le budget devient obligatoire
  seulement au passage en statut "in_progress" (vérifié dans le service).
"""

from datetime import datetime

from pydantic import BaseModel, field_validator

from app.shared.enums import ProjectStatus


# ─────────────────────────────────────────────────────────────────────────────
# REQUESTS
# ─────────────────────────────────────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    """
    Données pour créer un nouveau projet.

    Seul title est obligatoire — on peut créer un projet
    à l'état d'idée sans connaître encore le budget ni les dates.
    """
    title: str
    description: str | None = None
    budget_allocated: float | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Le titre doit contenir au moins 3 caractères")
        return v

    @field_validator("budget_allocated")
    @classmethod
    def budget_must_be_positive(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("Le budget ne peut pas être négatif")
        return v


class UpdateProjectRequest(BaseModel):
    """
    Données pour modifier un projet existant.

    Tous les champs sont optionnels — on n'envoie que ce qu'on veut modifier.
    C'est le pattern PATCH : modification partielle.

    Exemple : modifier uniquement le statut
        PATCH /projects/{id}
        Body : {"status": "in_progress"}

    Règle métier : passer en "in_progress" sans budget → erreur
    Cette vérification est dans le service, pas ici.
    """
    title: str | None = None
    description: str | None = None
    budget_allocated: float | None = None
    status: ProjectStatus | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if len(v) < 3:
                raise ValueError("Le titre doit contenir au moins 3 caractères")
        return v

    @field_validator("budget_allocated")
    @classmethod
    def budget_must_be_positive(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("Le budget ne peut pas être négatif")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSES
# ─────────────────────────────────────────────────────────────────────────────

class ProjectResponse(BaseModel):
    """
    Représentation publique d'un projet.

    budget_remaining est calculé à la volée via la @property du modèle.
    Il est null si budget_allocated n'est pas encore défini.

    creator_name : nom du membre qui a créé le projet.
    Chargé séparément dans le service pour éviter une jointure complexe.
    """
    id: str
    title: str
    description: str | None
    status: str
    budget_allocated: float | None
    budget_spent: float
    budget_remaining: float | None
    creator_name: str
    start_date: str | None
    end_date: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_model(cls, project, creator_name: str = "") -> "ProjectResponse":
        """
        Convertit un objet Project SQLAlchemy en ProjectResponse Pydantic.

        budget_remaining appelle la @property du modèle — retourne None
        si budget_allocated est None, sinon allocated - spent.
        """
        return cls(
            id=str(project.id),
            title=project.title,
            description=project.description,
            status=project.status,
            budget_allocated=(
                float(project.budget_allocated)
                if project.budget_allocated is not None else None
            ),
            budget_spent=float(project.budget_spent),
            budget_remaining=project.budget_remaining,
            creator_name=creator_name,
            start_date=(
                project.start_date.isoformat()
                if project.start_date else None
            ),
            end_date=(
                project.end_date.isoformat()
                if project.end_date else None
            ),
            created_at=project.created_at.isoformat(),
            updated_at=project.updated_at.isoformat(),
        )