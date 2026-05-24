"""
Create missing tables and seed admin user.
Usage: python -m backend.seed
"""

from sqlalchemy import select

from backend.auth import hash_password
from backend.database import SessionLocal, engine
from backend.models import Base, RolUsuario, Usuario
from backend.utils import create_test_user


def main():
    print("Creando tablas (si no existen)...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        stmt = select(Usuario).where(Usuario.email == "admin@arenas360.com")
        admin = db.execute(stmt).scalars().first()
        if not admin:
            create_test_user(
                db,
                email="admin@arenas360.com",
                password="Admin123!",
                primer_nombre="Admin",
                primer_apellido="Arenas",
                rol=RolUsuario.SUPERADMIN,
            )
            print("✅ Usuario admin creado")
        else:
            admin.hashed_password = hash_password("Admin123!")
            admin.rol = RolUsuario.SUPERADMIN
            db.commit()
            print("ℹ️  Usuario admin actualizado")
    finally:
        db.close()

    print("✅ Seed completado (eventos se leen de la tabla existente `eventos`)")


if __name__ == "__main__":
    main()
