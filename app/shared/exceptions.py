"""
shared/exceptions.py — Exceptions personnalisées de l'application.

Principe : chaque situation d'erreur métier a sa propre exception.
Le gestionnaire global dans main.py les convertit toutes en réponse JSON.

Hiérarchie :
    AppException (base)
    ├── NotFoundError        → HTTP 404
    ├── ForbiddenError       → HTTP 403
    ├── UnauthorizedError    → HTTP 401
    ├── ConflictError        → HTTP 409
    ├── ValidationError      → HTTP 422
    ├── BusinessRuleError    → HTTP 400
    ├── InsufficientFundsError → HTTP 400
    └── InvalidTokenError    → HTTP 401

Usage dans un service :
    member = await db.get(Member, member_id)
    if not member:
        raise NotFoundError("Membre", member_id)
    # → FastAPI renvoie automatiquement :
    # { "success": false, "message": "Membre introuvable (id: xxx)", ... }
"""

from typing import Any


class AppException(Exception):
    """
    Exception de base — toutes les exceptions métier en héritent.

    Attributs :
        message     : message lisible pour l'utilisateur final
        status_code : code HTTP à retourner (404, 403, 400...)
        error_code  : code technique pour le frontend (ex: "NOT_FOUND")
                      Le frontend peut afficher un message traduit selon ce code.
        details     : informations supplémentaires optionnelles (pour le debug)
    """

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        error_code: str = "APP_ERROR",
        details: Any = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details
        # On appelle le constructeur parent pour que isinstance() fonctionne
        super().__init__(message)


class NotFoundError(AppException):
    """
    Ressource introuvable en base de données.

    Usage :
        raise NotFoundError("Membre", member_id)
        → message : "Membre introuvable (id: 550e8400-...)"

        raise NotFoundError("Projet")
        → message : "Projet introuvable"
    """

    def __init__(self, resource: str, identifier: Any = None) -> None:
        message = f"{resource} introuvable"
        if identifier:
            message += f" (id: {identifier})"
        super().__init__(
            message=message,
            status_code=404,
            error_code="NOT_FOUND",
        )


class ForbiddenError(AppException):
    """
    Le membre est connecté mais n'a pas le droit d'effectuer cette action.
    Différent de UnauthorizedError : ici on SAIT qui c'est, mais il n'a pas accès.

    Ex : un membre normal essaie d'accéder à une route réservée au comptable.
    """

    def __init__(self, message: str = "Accès refusé") -> None:
        super().__init__(
            message=message,
            status_code=403,
            error_code="FORBIDDEN",
        )


class UnauthorizedError(AppException):
    """
    Le membre n'est pas connecté ou son token est invalide.
    Différent de ForbiddenError : ici on ne sait pas (encore) qui c'est.

    Ex : requête sans token, token expiré.
    """

    def __init__(self, message: str = "Authentification requise") -> None:
        super().__init__(
            message=message,
            status_code=401,
            error_code="UNAUTHORIZED",
        )


class ConflictError(AppException):
    """
    Conflit avec une donnée existante en base.

    Ex : essayer d'inviter un membre avec un numéro de téléphone déjà enregistré.

    Usage :
        raise ConflictError("Ce numéro de téléphone est déjà utilisé")
    """

    def __init__(self, message: str) -> None:
        super().__init__(
            message=message,
            status_code=409,
            error_code="CONFLICT",
        )


class ValidationError(AppException):
    """
    Les données envoyées sont invalides (logique métier, pas format Pydantic).
    Pydantic gère déjà la validation de format — cette exception couvre
    les validations métier supplémentaires.

    Ex : date de fin avant date de début, montant négatif.

    Usage :
        raise ValidationError("La date de fin doit être après la date de début")
    """

    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(
            message=message,
            status_code=422,
            error_code="VALIDATION_ERROR",
            details=details,
        )


class BusinessRuleError(AppException):
    """
    Violation d'une règle métier de l'association.
    Pour les cas qui ne rentrent pas dans les autres catégories.

    Ex :
        - Passer un projet en "in_progress" sans budget défini
        - Confirmer une cotisation d'un mois futur
        - Modifier les paramètres de cotisation en pleine période de collecte

    Usage :
        raise BusinessRuleError(
            "Impossible de démarrer un projet sans budget défini."
        )
    """

    def __init__(self, message: str) -> None:
        super().__init__(
            message=message,
            status_code=400,
            error_code="BUSINESS_RULE_ERROR",
        )


class InsufficientFundsError(AppException):
    """
    La caisse n'a pas assez de fonds pour effectuer la dépense demandée.

    Usage :
        raise InsufficientFundsError(available=150_000, requested=200_000)
        → message : "Fonds insuffisants. Disponible : 150 000 FCFA, Demandé : 200 000 FCFA"
    """

    def __init__(self, available: float, requested: float) -> None:
        super().__init__(
            message=(
                f"Fonds insuffisants. "
                f"Disponible : {available:,.0f} FCFA, "
                f"Demandé : {requested:,.0f} FCFA"
            ),
            status_code=400,
            error_code="INSUFFICIENT_FUNDS",
        )


class InvalidTokenError(AppException):
    """
    Token JWT invalide, expiré ou révoqué.
    Également utilisé pour les tokens d'invitation invalides.

    Usage :
        raise InvalidTokenError()
        raise InvalidTokenError("Lien d'invitation expiré")
    """

    def __init__(self, message: str = "Token invalide ou expiré") -> None:
        super().__init__(
            message=message,
            status_code=401,
            error_code="INVALID_TOKEN",
        )
