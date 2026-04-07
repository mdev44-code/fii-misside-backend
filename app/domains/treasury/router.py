"""
treasury/router.py — Endpoints HTTP pour la gestion de la caisse.

Contrôle d'accès :
  GET  /treasury/balance       → tous les membres (transparence)
  GET  /treasury/transactions  → tous les membres (transparence)
  POST /treasury/init          → comptable uniquement
  POST /treasury/deposit       → comptable uniquement
  POST /treasury/expense       → comptable uniquement

La transparence financière est un choix délibéré :
  tous les membres peuvent voir le solde et l'historique.
  Seul le comptable (+ admin) peut modifier.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.treasury.schemas import (
    ConfirmDepositRequest,
    ExpenseRequest,
    InitBalanceRequest,
)
from app.domains.treasury.service import TreasuryService
from app.infrastructure.database.session import get_db
from app.infrastructure.security.permissions import require_treasurer
from app.shared.response import success_response

router = APIRouter()


@router.get("/balance")
async def get_balance(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Retourne le solde actuel de la caisse.
    Accessible à tous les membres.
    """
    service = TreasuryService(db)
    balance = await service.get_balance()

    return success_response(data=balance.model_dump())


@router.post("/init", dependencies=[Depends(require_treasurer)])
async def initialize_balance(
    data: InitBalanceRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Initialise ou réinitialise la caisse avec le solde réel.
    Réservé au comptable et aux administrateurs.

    À utiliser une fois au démarrage pour synchroniser l'app
    avec l'argent déjà collecté avant la digitalisation.
    """
    service = TreasuryService(db)
    balance = await service.initialize_balance(
        initial_balance=data.initial_balance,
        treasurer=current_member,
    )

    return success_response(
        data=balance.model_dump(),
        message=f"Caisse initialisée à {data.initial_balance:,.0f} FCFA",
    )


@router.post("/deposit", dependencies=[Depends(require_treasurer)])
async def confirm_deposit(
    data: ConfirmDepositRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Confirme la réception d'un transfert Wave.
    Met à jour la caisse et confirme la cotisation du membre.
    Réservé au comptable et aux administrateurs.
    """
    service = TreasuryService(db)
    transaction = await service.confirm_deposit(
        data=data,
        treasurer=current_member,
    )

    return success_response(
        data=transaction.model_dump(),
        message=(
            f"Dépôt de {data.amount:,.0f} FCFA confirmé. "
            f"Nouveau solde : {transaction.balance_after:,.0f} FCFA"
        ),
    )


@router.post("/expense", dependencies=[Depends(require_treasurer)])
async def record_expense(
    data: ExpenseRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Enregistre une dépense après retrait Wave physique.
    Réservé au comptable et aux administrateurs.
    """
    service = TreasuryService(db)
    transaction = await service.record_expense(
        data=data,
        treasurer=current_member,
    )

    return success_response(
        data=transaction.model_dump(),
        message=(
            f"Dépense de {data.amount:,.0f} FCFA enregistrée. "
            f"Nouveau solde : {transaction.balance_after:,.0f} FCFA"
        ),
    )


@router.get("/transactions")
async def list_transactions(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
    # Query() : paramètres optionnels dans l'URL (?type=deposit&page=1)
    transaction_type: str | None = Query(default=None, alias="type"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
):
    """
    Retourne l'historique paginé des transactions.
    Accessible à tous les membres.

    Paramètres URL optionnels :
      ?type=deposit   → seulement les dépôts
      ?type=expense   → seulement les dépenses
      ?page=2         → page 2
      ?per_page=50    → 50 par page (max 100)
    """
    service = TreasuryService(db)
    offset = (page - 1) * per_page

    transactions, total = await service.get_transactions(
        limit=per_page,
        offset=offset,
        transaction_type=transaction_type,
    )

    import math
    return success_response(
        data={
            "transactions": [t.model_dump() for t in transactions],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": math.ceil(total / per_page) if total > 0 else 0,
        }
    )