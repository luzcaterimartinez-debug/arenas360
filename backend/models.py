from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, Date, Text, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
import enum

Base = declarative_base()


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(255), unique=True, nullable=False, index=True)
    expira_en = Column(DateTime(timezone=True), nullable=False, index=True)
    usado = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class RolUsuario(str, enum.Enum):
    SUPERADMIN = "SUPERADMIN"
    ADMIN_TENANT = "ADMIN_TENANT"
    OPERADOR = "OPERADOR"
    READONLY = "READONLY"
    JUEZ = "JUEZ"


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, nullable=False)
    email = Column(String(150), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    rol = Column(Enum(RolUsuario), nullable=False, default=RolUsuario.READONLY)
    activo = Column(Boolean, nullable=False, default=True)
    primer_nombre = Column(String(60), nullable=False)
    segundo_nombre = Column(String(60), nullable=True)
    primer_apellido = Column(String(60), nullable=False)
    segundo_apellido = Column(String(60), nullable=True)
    telefono = Column(String(30), nullable=True)
    foto = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    @property
    def nombre_completo(self):
        """Return full name"""
        nombre = f"{self.primer_nombre}"
        if self.segundo_nombre:
            nombre += f" {self.segundo_nombre}"
        nombre += f" {self.primer_apellido}"
        if self.segundo_apellido:
            nombre += f" {self.segundo_apellido}"
        return nombre


class Escenario(Base):
    __tablename__ = "escenarios"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(200), nullable=False)
    direccion = Column(String(255), nullable=True)
    municipio = Column(String(120), nullable=True)
    departamento = Column(String(120), nullable=True)
    activo = Column(Boolean, nullable=False, default=True)


class Evento(Base):
    """Maps to existing `eventos` table in PostgreSQL."""

    __tablename__ = "eventos"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, nullable=False)
    nombre = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=True)
    descripcion = Column(Text, nullable=True)
    fecha_inicio = Column(Date, nullable=True)
    fecha_fin = Column(Date, nullable=True)
    fecha_inicio_inscripciones = Column(DateTime(timezone=True), nullable=True)
    fecha_fin_inscripciones = Column(DateTime(timezone=True), nullable=True)
    categoria_evento = Column(String(30), nullable=True)
    estado = Column(String(40), nullable=False)
    escenario_id = Column(Integer, ForeignKey("escenarios.id"), nullable=True)
    foto = Column(String(500), nullable=True)
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    escenario = None  # populated via joinedload in queries


class EventoPrueba(Base):
    __tablename__ = "evento_pruebas"

    id = Column(Integer, primary_key=True, index=True)
    evento_id = Column(Integer, ForeignKey("eventos.id"), nullable=False)


class Inscripcion(Base):
    __tablename__ = "inscripciones"

    id = Column(Integer, primary_key=True, index=True)
    deportista_id = Column(Integer, nullable=False)
    evento_id = Column(Integer, ForeignKey("eventos.id"), nullable=False)


class TipoSeguimiento(str, enum.Enum):
    ATLETA = "ATLETA"
    EVENTO = "EVENTO"
    PRUEBA = "PRUEBA"


class TipoNotificacion(str, enum.Enum):
    event = "event"
    result = "result"
    system = "system"


class UsuarioSeguimiento(Base):
    __tablename__ = "usuario_seguimientos"
    __table_args__ = (
        UniqueConstraint("usuario_id", "tipo", "entidad_id", name="uq_usuario_seguimiento"),
    )

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False, index=True)
    tipo = Column(Enum(TipoSeguimiento), nullable=False)
    entidad_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Notificacion(Base):
    __tablename__ = "notificaciones"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False, index=True)
    tipo = Column(Enum(TipoNotificacion), nullable=False)
    titulo = Column(String(255), nullable=False)
    descripcion = Column(Text, nullable=False)
    payload = Column(Text, nullable=True)
    leida = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UsuarioPreferencia(Base):
    __tablename__ = "usuario_preferencias"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False, unique=True, index=True)
    notificaciones_push = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class DispositivoPush(Base):
    __tablename__ = "dispositivos_push"
    __table_args__ = (
        UniqueConstraint("push_token", name="uq_dispositivo_push_token"),
    )

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False, index=True)
    push_token = Column(String(512), nullable=False)
    platform = Column(String(20), nullable=True)
    activo = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(200), nullable=False)
    schema_name = Column(String(63), nullable=False)
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Invitacion(Base):
    __tablename__ = "invitaciones"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    email = Column(String(150), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    rol = Column(String(50), nullable=False)
    estado = Column(String(20), nullable=False, default="pendiente")
    invitado_por_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)


class UsuarioTenantRol(Base):
    __tablename__ = "usuario_tenant_rol"
    __table_args__ = (
        UniqueConstraint("usuario_id", "tenant_id", name="uq_usuario_tenant_rol_usuario_id_tenant_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    rol = Column(Enum(RolUsuario), nullable=False)
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ResultadoNotificacionTracker(Base):
    """Tracks last published result timestamp per event test to avoid duplicate alerts."""

    __tablename__ = "resultado_notificacion_tracker"
    __table_args__ = (
        UniqueConstraint("evento_id", "prueba_id", name="uq_resultado_notif_evento_prueba"),
    )

    id = Column(Integer, primary_key=True, index=True)
    evento_id = Column(Integer, ForeignKey("eventos.id"), nullable=False, index=True)
    prueba_id = Column(Integer, nullable=False, index=True)
    ultimo_resultado_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
