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
    SUSPENDED = "suspended"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"


# Transitions de statut autorisées pour les projets
# Clé = statut actuel, Valeur = liste des statuts vers lesquels on peut aller
PROJECT_STATUS_TRANSITIONS: dict[ProjectStatus, list[ProjectStatus]] = {
    ProjectStatus.DRAFT: [ProjectStatus.IN_PROGRESS, ProjectStatus.CANCELLED],
    ProjectStatus.IN_PROGRESS: [
        ProjectStatus.COMPLETED,
        ProjectStatus.SUSPENDED,
        ProjectStatus.ABANDONED,
    ],
    ProjectStatus.SUSPENDED: [ProjectStatus.IN_PROGRESS, ProjectStatus.ABANDONED],
    ProjectStatus.COMPLETED: [],  # terminal
    ProjectStatus.ABANDONED: [],  # terminal
    ProjectStatus.CANCELLED: [],  # terminal
}

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
    PENDING = "pending"
    DECLARED = "declared"
    CONFIRMED = "confirmed"
    LATE = "late"


class ContributionMode(StrEnum):
    FREE = "free"
    FIXED = "fixed"


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────


class NotificationChannel(StrEnum):
    IN_APP = "in_app"


class NotificationType(StrEnum):
    CONTRIBUTION_REMINDER = "contribution_reminder"  # rappel de cotiser (broadcast)
    CONTRIBUTION_RECEIVED = "contribution_received"  # cotisation enregistrée (broadcast)
    EXPENSE_RECORDED = "expense_recorded"  # dépense enregistrée (broadcast)
    NEW_PROJECT = "new_project"  # nouveau projet créé (broadcast)
    MEMBER_JOINED = "member_joined"  # nouveau membre (broadcast)
    GENERAL = "general"  # message général


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    READ = "read"


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT
# ─────────────────────────────────────────────────────────────────────────────


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGOUT = "logout"
    CONFIRM = "confirm"  # ex: confirmation d'une cotisation
    APPROVE = "approve"  # ex: approbation d'une dépense
    REJECT = "reject"  # ex: rejet d'une dépense
