"""
Utility functions for testing and development
"""

from backend.models import Usuario, RolUsuario
from backend.auth import hash_password
from sqlalchemy.orm import Session
from sqlalchemy import select


def create_test_user(
    db: Session,
    email: str = "test@example.com",
    password: str = "password123",
    tenant_id: int = 1,
    primer_nombre: str = "Test",
    primer_apellido: str = "User",
    rol: RolUsuario = RolUsuario.READONLY
) -> Usuario:
    """Create a test user for development"""
    
    # Check if user already exists
    stmt = select(Usuario).where(Usuario.email == email)
    existing = db.execute(stmt).scalars().first()
    if existing:
        return existing
    
    usuario = Usuario(
        email=email,
        hashed_password=hash_password(password),
        tenant_id=tenant_id,
        primer_nombre=primer_nombre,
        primer_apellido=primer_apellido,
        rol=rol,
        activo=True
    )
    
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


def create_admin_user(db: Session) -> Usuario:
    """Create an admin test user"""
    return create_test_user(
        db,
        email="admin@example.com",
        password="admin123",
        primer_nombre="Admin",
        rol=RolUsuario.SUPERADMIN
    )


def delete_test_user(db: Session, email: str) -> bool:
    """Delete a test user"""
    stmt = select(Usuario).where(Usuario.email == email)
    usuario = db.execute(stmt).scalars().first()
    if usuario:
        db.delete(usuario)
        db.commit()
        return True
    return False
