from enum import StrEnum


# ─────────────────────────────────────────────────────────────────────────────
# MEMBRES
# ─────────────────────────────────────────────────────────────────────────────

class Role(StrEnum):
    ADMIN = "admin"
    TREASURER = "treasurer"
    MANAGER = "manager"
    MEMBER = "member"


class MemberStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


# ─────────────────────────────────────────────────────────────────────────────
# PROJETS
# ─────────────────────────────────────────────────────────────────────────────

class ProjectStatus(StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# ─────────────────────────────────────────────────────────────────────────────
# CAISSE & TRANSACTIONS
# ─────────────────────────────────────────────────────────────────────────────

class TransactionType(StrEnum):
    DEPOSIT = "deposit"
    EXPENSE = "expense"
    ADJUSTMENT = "adjustment"


class TransactionStatus(StrEnum):
    CONFIRMED = "confirmed"
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"


# ─────────────────────────────────────────────────────────────────────────────
# COTISATIONS
# ─────────────────────────────────────────────────────────────────────────────

class ContributionStatus(StrEnum):
    """
    Le cycle de vie d'une cotisation mensuelle :

    PENDING   → le membre n'a encore rien fait ce mois-ci
    DECLARED  → le membre a cliqué "j'ai envoyé" mais le comptable n'a pas confirmé
    CONFIRMED → le comptable a confirmé la réception sur Wave
    LATE      → la période est passée (après le 15) et le membre n'a pas cotisé

    Flux normal : PENDING → DECLARED → CONFIRMED
    Flux retard  : PENDING → LATE (déclenché automatiquement par le cron job)
    """
    PENDING = "pending"
    DECLARED = "declared"
    CONFIRMED = "confirmed"
    LATE = "late"


class ContributionMode(StrEnum):
    """
    Les deux modes de cotisation de l'association :

    FREE  → chacun cotise ce qu'il peut (mode actuel)
            → pas de montant attendu, tout montant est accepté

    FIXED → montant fixe défini pour tous (mode futur)
            → expected_amount est renseigné dans chaque contribution
            → on peut savoir si une cotisation est complète ou partielle
    """
    FREE = "free"
    FIXED = "fixed"


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

class NotificationChannel(StrEnum):
    """
    Les canaux d'envoi disponibles :

    SMS    → envoyé via AfricasTalking sur le numéro de téléphone du membre
    IN_APP → notification stockée en base, visible dans l'application
    """
    SMS = "sms"
    IN_APP = "in_app"


class NotificationType(StrEnum):
    """
    Les types de notifications envoyées.
    Utilisé pour personnaliser le message et l'icône dans l'interface.
    """
    CONTRIBUTION_REMINDER  = "contribution_reminder"   # rappel de cotiser
    CONTRIBUTION_CONFIRMED = "contribution_confirmed"  # cotisation validée
    EXPENSE_SUBMITTED      = "expense_submitted"       # dépense soumise
    EXPENSE_APPROVED       = "expense_approved"        # dépense approuvée
    EXPENSE_REJECTED       = "expense_rejected"        # dépense rejetée
    NEW_PROJECT            = "new_project"             # nouveau projet créé
    MEMBER_JOINED          = "member_joined"           # nouveau membre
    GENERAL                = "general"                 # message général


class NotificationStatus(StrEnum):
    """
    PENDING → en attente d'envoi
    SENT    → envoyé avec succès
    FAILED  → échec d'envoi (SMS non délivré, etc.)
    READ    → lu par le membre (in_app uniquement)
    """
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    READ = "read"


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT
# ─────────────────────────────────────────────────────────────────────────────

class AuditAction(StrEnum):
    """
    Les actions tracées dans les audit_logs.
    Permet de répondre à : "qui a fait quoi et quand ?"
    """
    CREATE  = "create"
    UPDATE  = "update"
    DELETE  = "delete"
    LOGIN   = "login"
    LOGOUT  = "logout"
    CONFIRM = "confirm"   # ex: confirmation d'une cotisation
    APPROVE = "approve"   # ex: approbation d'une dépense
    REJECT  = "reject"    # ex: rejet d'une dépense