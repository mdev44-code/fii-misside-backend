"""
shared/response.py — Structure uniforme pour toutes les réponses de l'API.

Problème sans ce fichier : chaque route renverrait ses données comme elle veut.
    Route A : {"member": {...}}
    Route B : {"data": {...}, "status": "ok"}
    Route C : [...]

Avec ce fichier : toujours le même format, le frontend sait quoi attendre.
    Succès  : {"success": true,  "message": "...", "data": ...}
    Erreur  : {"success": false, "message": "...", "data": null}
    Liste   : {"success": true,  "message": "...", "data": [...],
               "total": N, "page": 1, "per_page": 20, "total_pages": 3}

Generic[T] : T est un "type variable" — il s'adapte au type de données retourné.
    APIResponse[Member]  → data est un Member
    APIResponse[list[Member]] → data est une liste de Members
    APIResponse[None]    → data est null
"""

import math
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

# TypeVar : un placeholder de type, remplacé par le vrai type à l'usage
T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """
    Enveloppe standard pour toutes les réponses.

    BaseModel de Pydantic : sérialise automatiquement en JSON.
    Generic[T] : le type de 'data' est flexible.

    Exemple d'utilisation dans une route :
        return APIResponse(
            success=True,
            message="Membre créé",
            data=member_response,
        )
    """
    success: bool
    message: str
    data: T | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Réponse pour les listes avec pagination.
    Utilisée quand une liste peut contenir beaucoup d'éléments
    (historique des transactions, liste des membres...).

    Exemple de réponse JSON :
        {
            "success": true,
            "message": "OK",
            "data": [{...}, {...}],
            "total": 45,        ← nombre total d'éléments en base
            "page": 2,          ← page actuelle
            "per_page": 20,     ← éléments par page
            "total_pages": 3    ← nombre de pages au total
        }
    """
    success: bool = True
    message: str = "OK"
    data: list[T]
    total: int
    page: int
    per_page: int
    total_pages: int


class PageParams(BaseModel):
    """
    Paramètres de pagination reçus dans les requêtes GET.
    Injectés via Depends() dans les routes qui retournent des listes.

    Exemple d'utilisation dans une route :
        @router.get("/transactions")
        async def list_transactions(
            params: PageParams = Depends(),  # lit ?page=2&per_page=20 dans l'URL
            db: AsyncSession = Depends(get_db),
        ):
            transactions, total = await service.get_all(
                limit=params.per_page,
                offset=params.offset,  # ← calculé automatiquement
            )
            return params.to_response(transactions, total)
    """
    page: int = 1        # page 1 par défaut
    per_page: int = 20   # 20 éléments par page par défaut

    @property
    def offset(self) -> int:
        """
        Calcule le décalage SQL pour la pagination.
        Page 1 → offset 0 (on commence au début)
        Page 2 → offset 20 (on saute les 20 premiers)
        Page 3 → offset 40 (on saute les 40 premiers)

        Formule : (page - 1) × per_page
        """
        return (self.page - 1) * self.per_page

    def to_response(self, data: list[Any], total: int) -> dict[str, Any]:
        """
        Construit le dictionnaire de réponse paginée.
        math.ceil(45 / 20) = 3 pages (2 pleines + 1 avec 5 éléments)
        """
        return {
            "success": True,
            "message": "OK",
            "data": data,
            "total": total,
            "page": self.page,
            "per_page": self.per_page,
            "total_pages": math.ceil(total / self.per_page) if total > 0 else 0,
        }


# ── Fonctions utilitaires ─────────────────────────────────────────────────────
# Ces deux fonctions sont les plus utilisées dans les routes.
# Elles évitent d'écrire le dictionnaire à la main à chaque fois.

def success_response(data: Any = None, message: str = "OK") -> dict[str, Any]:
    """
    Raccourci pour une réponse de succès.

    Usage dans une route :
        return success_response(data=membre, message="Membre créé avec succès")
        → {"success": true, "message": "Membre créé avec succès", "data": {...}}

        return success_response()
        → {"success": true, "message": "OK", "data": null}
    """
    return {"success": True, "message": message, "data": data}


def error_response(message: str, data: Any = None) -> dict[str, Any]:
    """
    Raccourci pour une réponse d'erreur.
    Principalement utilisé dans le gestionnaire d'exceptions de main.py.

    Usage :
        return error_response("Token invalide")
        → {"success": false, "message": "Token invalide", "data": null}
    """
    return {"success": False, "message": message, "data": data}