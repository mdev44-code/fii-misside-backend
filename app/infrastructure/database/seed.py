import asyncio
import sys

from argon2 import PasswordHasher
from sqlalchemy import select

_hasher = PasswordHasher()

DEFAULT_PASSWORD = "AdminPwd!"


async def seed() -> None:
    from app.config import settings
    from app.infrastructure.database.models import AssociationSettings, Member
    from app.infrastructure.database.session import AsyncSessionLocal
    from app.shared.enums import MemberStatus, Role

    if not settings.first_admin_phone:
        print("❌ FIRST_ADMIN_PHONE non configuré dans .env")
        print("   Ajoutez : FIRST_ADMIN_PHONE=+221XXXXXXXXX")
        sys.exit(1)

    async with AsyncSessionLocal() as db:

        # ── Vérifie si un admin existe déjà ──────────────────────────────────
        result = await db.execute(
            select(Member).where(Member.role == Role.ADMIN)
        )
        existing_admin = result.scalar_one_or_none()

        if existing_admin:
            print(f"ℹ️  Un administrateur existe déjà : {existing_admin.full_name}")
            print("   Seed ignoré.")
        else:
            # ── Crée le premier admin ─────────────────────────────────────────
            admin = Member(
                full_name="Madina Diallo",
                phone_number=settings.first_admin_phone,
                email=settings.first_admin_email or None,
                password_hash=_hasher.hash(settings.first_admin_password or DEFAULT_PASSWORD),
                role=Role.ADMIN,
                status=MemberStatus.ACTIVE,
            )
            db.add(admin)
            print("✅ Administrateur créé :")
            print(f"   Téléphone : {settings.first_admin_phone}")
            if settings.first_admin_email:
                print(f"   Email     : {settings.first_admin_email}")
            print(f"   Mot de passe : {settings.first_admin_password or DEFAULT_PASSWORD}")
            print("   ⚠️  Changez ce mot de passe immédiatement !")

        # ── Crée les settings par défaut si absents ───────────────────────────
        settings_result = await db.execute(select(AssociationSettings))
        if not settings_result.scalar_one_or_none():
            asso_settings = AssociationSettings(
                contribution_mode=settings.contribution_mode,
                fixed_amount=(
                    settings.contribution_fixed_amount
                    if settings.contribution_mode == "fixed"
                    else None
                ),
                currency="XOF",
            )
            db.add(asso_settings)
            print(
                f"\n✅ Paramètres association créés :"
                f"\n   Mode cotisation : {settings.contribution_mode}"
            )

        await db.commit()
        print("\n🎉 Seed terminé avec succès !")


if __name__ == "__main__":
    asyncio.run(seed())