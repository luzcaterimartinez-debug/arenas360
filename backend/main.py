from fastapi import FastAPI, Depends, HTTPException, status, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
import asyncio
import logging
import os

from backend.database import get_db, engine, ensure_schema_updates
from backend.models import Base, Usuario, RolUsuario, Evento
from backend.schemas import (
    LoginRequest,
    LoginResponse,
    PasswordResetRequest,
    PasswordResetConfirm,
    PasswordResetResponse,
    UsuarioCreate,
    UsuarioResponse,
    TenantPublicItem,
    TenantsPublicResponse,
    InvitacionPreviewResponse,
    EventoResponse,
    EventoDetalleResponse,
    EventoCronogramaResponse,
    CompetenciaDetalleResponse,
    EventosResumenResponse,
    AtletaPerfilResponse,
    AtletaCompareResponse,
    AtletaListItem,
    AtletasIndexResponse,
    DisciplinasIndexResponse,
    DisciplinaDetalleResponse,
    ResultadosIndexResponse,
    ResultadoDetalleResponse,
    AtletaConPodiosItem,
    SeguimientoToggleRequest,
    SeguimientoEstadoResponse,
    SeguimientoEstadosResponse,
    MisSeguidosResponse,
    NotificacionResponse,
    NotificacionesResumenResponse,
    NotificacionesListResponse,
    UsuarioPreferenciasResponse,
    UsuarioPreferenciasUpdate,
    DispositivoPushRegisterRequest,
    UsuarioPerfilUpdate,
    ResultadosNotificarRequest,
    ResultadosNotificarResponse,
    ProcesarNotificacionesResultadosResponse,
    PushPruebaResponse,
)
from backend.auth import (
    verify_password,
    create_access_token,
    hash_password,
    get_current_user,
    get_optional_user,
    require_admin,
    resolve_tenant_filter,
    validate_secret_key,
)
from backend.eventos import fetch_eventos, fetch_evento_detalle, fetch_evento_cronograma, fetch_competencia_detalle
from backend.atletas import fetch_atletas_list, fetch_atletas_resumen, fetch_atleta_perfil, fetch_atletas_comparacion
from backend.disciplinas import fetch_disciplinas_list, fetch_disciplinas_resumen, fetch_disciplina_detalle
from backend.resultados import (
    fetch_competiciones_con_resultados,
    fetch_atletas_con_podios,
    fetch_resultado_detalle,
    fetch_resultados_por_evento,
    fetch_resultados_resumen,
)
from backend.media import serve_media_file
from backend.models import TipoSeguimiento
from backend import seguimientos as seguimientos_service
from backend import preferencias as preferencias_service
from backend import resultados_notificaciones as resultados_notificaciones_service
from backend import usuarios as usuarios_service
from backend import tenants as tenants_service
from backend import password_reset as password_reset_service
from backend.database import SessionLocal
from sqlalchemy import text

logger = logging.getLogger(__name__)
RESULT_NOTIFICATIONS_INTERVAL_SECONDS = int(
    os.getenv("RESULT_NOTIFICATIONS_INTERVAL_SECONDS", "300")
)

app = FastAPI(
    title="Arenas 360 API",
    description="Backend API para la aplicación Arenas 360",
    version="1.0.0"
)

@app.on_event("startup")
def on_startup():
    """Ensure tables exist when the API starts."""
    validate_secret_key()
    Base.metadata.create_all(bind=engine)
    ensure_schema_updates()


async def _result_notifications_worker() -> None:
    await asyncio.sleep(15)
    while True:
        db = SessionLocal()
        try:
            stats = resultados_notificaciones_service.procesar_notificaciones_resultados_nuevos(db)
            if stats["procesadas"]:
                logger.info("Notificaciones de resultados: %s", stats)
        except Exception:
            logger.exception("Error procesando notificaciones de resultados")
        finally:
            db.close()
        await asyncio.sleep(RESULT_NOTIFICATIONS_INTERVAL_SECONDS)


@app.on_event("startup")
async def start_background_workers():
    asyncio.create_task(_result_notifications_worker())


# CORS middleware — credentials cannot be used with allow_origins=["*"]
_cors_raw = os.getenv("CORS_ORIGINS", "*").strip()
if _cors_raw == "*":
    _cors_origins = ["*"]
    _cors_credentials = False
else:
    _cors_origins = [origin.strip() for origin in _cors_raw.split(",") if origin.strip()]
    _cors_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Health check endpoint with database connectivity."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc


@app.post("/api/auth/login", response_model=LoginResponse)
async def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    """
    Login endpoint - validates email and password
    Returns JWT access token and user information
    """
    # Find user by email
    stmt = select(Usuario).where(Usuario.email == credentials.email)
    usuario = db.execute(stmt).scalars().first()

    # Check if user exists and password is correct
    if not usuario or not verify_password(credentials.password, usuario.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if user is active
    if not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo",
        )

    # Create access token
    access_token = create_access_token(
        data={
            "sub": usuario.id,
            "email": usuario.email,
            "rol": usuario.rol.value
        }
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        usuario=usuarios_service.serialize_usuario(usuario),
    )


@app.get("/api/auth/me", response_model=UsuarioResponse)
async def get_me(request: Request, current_user: Usuario = Depends(get_current_user)):
    """Return the authenticated user from the JWT."""
    return usuarios_service.serialize_usuario(current_user, api_base=_api_base(request))


@app.patch("/api/auth/me", response_model=UsuarioResponse)
async def update_me(
    payload: UsuarioPerfilUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Update authenticated user profile fields."""
    usuario = usuarios_service.actualizar_perfil(db, current_user, payload)
    return usuarios_service.serialize_usuario(usuario, api_base=_api_base(request))


@app.post("/api/auth/me/foto", response_model=UsuarioResponse)
async def upload_me_foto(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Upload or replace the authenticated user's profile photo."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Archivo vacío")

    usuario = usuarios_service.actualizar_foto_perfil(db, current_user, file.filename, content)
    return usuarios_service.serialize_usuario(usuario, api_base=_api_base(request))


@app.get("/api/tenants/public", response_model=TenantsPublicResponse)
async def list_public_tenants(db: Session = Depends(get_db)):
    """List active organizations available during self-registration."""
    items = tenants_service.list_active_tenants(db)
    return TenantsPublicResponse(items=[TenantPublicItem(**item) for item in items])


@app.get("/api/auth/invitations/preview", response_model=InvitacionPreviewResponse)
async def preview_invitation(
    token: str,
    email: EmailStr,
    db: Session = Depends(get_db),
):
    """Validate invitation token and email before completing registration."""
    try:
        preview = tenants_service.get_invitation_preview(db, token, email.lower().strip())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return InvitacionPreviewResponse(**preview)


@app.post("/api/auth/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def register(
    usuario_data: UsuarioCreate,
    db: Session = Depends(get_db)
):
    """
    Register new user endpoint.
    Requires organization selection or a valid invitation token.
    """
    email = usuario_data.email.lower().strip()

    # Check if email already exists
    stmt = select(Usuario).where(Usuario.email == email)
    existing_user = db.execute(stmt).scalars().first()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El email ya está registrado",
        )

    try:
        tenant_id, rol, invitation = tenants_service.resolve_registration_context(
            db,
            email=email,
            tenant_id=usuario_data.tenant_id,
            invitation_token=usuario_data.invitation_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Create new user
    new_usuario = Usuario(
        email=email,
        hashed_password=hash_password(usuario_data.password),
        tenant_id=tenant_id,
        rol=rol,
        primer_nombre=usuario_data.primer_nombre.strip(),
        segundo_nombre=usuario_data.segundo_nombre.strip() if usuario_data.segundo_nombre else None,
        primer_apellido=usuario_data.primer_apellido.strip(),
        segundo_apellido=usuario_data.segundo_apellido.strip() if usuario_data.segundo_apellido else None,
        telefono=usuario_data.telefono.strip() if usuario_data.telefono else None,
        activo=True
    )

    db.add(new_usuario)
    try:
        db.flush()
        tenants_service.ensure_usuario_tenant_rol(db, new_usuario.id, tenant_id, rol)
        if invitation:
            tenants_service.mark_invitation_accepted(db, invitation)
        db.commit()
        db.refresh(new_usuario)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se pudo crear el usuario con los datos enviados",
        )

    access_token = create_access_token(
        data={
            "sub": new_usuario.id,
            "email": new_usuario.email,
            "rol": new_usuario.rol.value
        }
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        usuario=usuarios_service.serialize_usuario(new_usuario),
    )


@app.post("/api/auth/password-reset/request", response_model=PasswordResetResponse)
async def request_password_reset(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    """Request a password reset link (token returned only in DEBUG mode)."""
    result = password_reset_service.request_password_reset(db, payload.email)
    return PasswordResetResponse(**result)


@app.post("/api/auth/password-reset/confirm")
async def confirm_password_reset(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    """Set a new password using a valid reset token."""
    try:
        password_reset_service.confirm_password_reset(db, payload.token, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"ok": True, "message": "Contraseña actualizada correctamente"}


@app.get("/api/usuarios/{usuario_id}", response_model=UsuarioResponse)
async def get_usuario(
    usuario_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Get user by ID (self or admin within tenant)."""
    stmt = select(Usuario).where(Usuario.id == usuario_id)
    usuario = db.execute(stmt).scalars().first()

    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado",
        )

    if current_user.id != usuario_id:
        if current_user.rol not in {RolUsuario.SUPERADMIN, RolUsuario.ADMIN_TENANT}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado")
        if (
            current_user.rol == RolUsuario.ADMIN_TENANT
            and usuario.tenant_id != current_user.tenant_id
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado")

    return usuarios_service.serialize_usuario(usuario, api_base=_api_base(request))


@app.get("/api/usuarios/", response_model=list[UsuarioResponse])
async def list_usuarios(
    tenant_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_admin),
):
    """List users (admin only, scoped to tenant)."""
    stmt = select(Usuario)

    if current_user.rol == RolUsuario.SUPERADMIN:
        if tenant_id:
            stmt = stmt.where(Usuario.tenant_id == tenant_id)
    else:
        stmt = stmt.where(Usuario.tenant_id == current_user.tenant_id)

    usuarios = db.execute(stmt).scalars().all()
    return [usuarios_service.serialize_usuario(u) for u in usuarios]


@app.post("/api/seguimientos/toggle", response_model=SeguimientoEstadoResponse)
async def toggle_seguimiento(
    payload: SeguimientoToggleRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Follow or unfollow an athlete, event, or competition test."""
    tipo = TipoSeguimiento(payload.tipo)
    following = seguimientos_service.toggle_seguimiento(
        db,
        current_user,
        tipo,
        payload.entidad_id,
    )
    return SeguimientoEstadoResponse(following=following)


@app.get("/api/seguimientos/estado", response_model=SeguimientoEstadoResponse)
async def get_seguimiento_estado(
    tipo: str,
    entidad_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    if tipo not in {"ATLETA", "EVENTO", "PRUEBA"}:
        raise HTTPException(status_code=400, detail="Tipo de seguimiento inválido")
    following = seguimientos_service.get_follow_state(
        db,
        current_user.id,
        TipoSeguimiento(tipo),
        entidad_id,
    )
    return SeguimientoEstadoResponse(following=following)


@app.get("/api/seguimientos/estados", response_model=SeguimientoEstadosResponse)
async def get_seguimientos_estados(
    tipo: str,
    ids: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    if tipo not in {"ATLETA", "EVENTO", "PRUEBA"}:
        raise HTTPException(status_code=400, detail="Tipo de seguimiento inválido")
    entidad_ids = [int(value) for value in ids.split(",") if value.strip().isdigit()]
    estados = seguimientos_service.get_follow_states(
        db,
        current_user.id,
        TipoSeguimiento(tipo),
        entidad_ids,
    )
    return SeguimientoEstadosResponse(estados=estados)


@app.get("/api/seguimientos/mis-seguidos", response_model=MisSeguidosResponse)
async def get_mis_seguidos(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """List all events, athletes and competitions followed by the current user."""
    data = seguimientos_service.list_mis_seguidos(db, current_user.id, api_base=_api_base(request))
    return MisSeguidosResponse(**data)


@app.get("/api/notificaciones", response_model=NotificacionesListResponse)
async def list_notificaciones(
    tipo: str | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    data = seguimientos_service.list_notificaciones(db, current_user.id, tipo)
    return NotificacionesListResponse(**data)


@app.get("/api/notificaciones/resumen", response_model=NotificacionesResumenResponse)
async def resumen_notificaciones(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    return NotificacionesResumenResponse(**seguimientos_service.resumen_notificaciones(db, current_user.id))


@app.patch("/api/notificaciones/{notificacion_id}/leer")
async def marcar_notificacion_leida(
    notificacion_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    updated = seguimientos_service.marcar_leida(db, current_user.id, notificacion_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Notificación no encontrada")
    return {"ok": True}


@app.patch("/api/notificaciones/leer-todas")
async def marcar_todas_notificaciones_leidas(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    total = seguimientos_service.marcar_todas_leidas(db, current_user.id)
    return {"actualizadas": total}


@app.post("/api/notificaciones/push-prueba", response_model=PushPruebaResponse)
async def enviar_push_prueba(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Send a test push notification to the current user's devices."""
    from backend.push import enviar_push_prueba_usuario

    result = enviar_push_prueba_usuario(db, current_user.id)
    if result["push_enviados"] == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["mensaje"])
    return PushPruebaResponse(**result)


@app.get("/api/usuarios/preferencias", response_model=UsuarioPreferenciasResponse)
async def get_preferencias(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    preferencias = preferencias_service.get_or_create_preferencias(db, current_user.id)
    return UsuarioPreferenciasResponse(notificaciones_push=preferencias.notificaciones_push)


@app.patch("/api/usuarios/preferencias", response_model=UsuarioPreferenciasResponse)
async def update_preferencias(
    payload: UsuarioPreferenciasUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    preferencias = preferencias_service.actualizar_preferencias(
        db,
        current_user.id,
        payload.notificaciones_push,
    )
    return UsuarioPreferenciasResponse(notificaciones_push=preferencias.notificaciones_push)


@app.post("/api/dispositivos/push-token")
async def register_push_token(
    payload: DispositivoPushRegisterRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    preferencias_service.registrar_dispositivo_push(
        db,
        current_user.id,
        payload.push_token,
        payload.platform,
    )
    return {"ok": True}


@app.delete("/api/dispositivos/push-token")
async def unregister_push_token(
    push_token: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    removed = preferencias_service.desactivar_dispositivo_push(
        db,
        current_user.id,
        push_token,
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Token no encontrado")
    return {"ok": True}


VALID_ESTADOS_UI = {"PRÓXIMO", "EN CURSO", "FINALIZADO"}


def _api_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _ensure_event_visible(db: Session, evento_id: int, tenant_id: int | None) -> None:
    if tenant_id is None:
        return
    evento = db.get(Evento, evento_id)
    if not evento or evento.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evento no encontrado",
        )


@app.get("/api/media/{file_path:path}")
async def get_media(file_path: str):
    """Serve event images stored as relative paths in the database."""
    return serve_media_file(file_path)


@app.get("/api/eventos/", response_model=list[EventoResponse])
async def list_eventos(
    request: Request,
    estado: str | None = None,
    tenant_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario | None = Depends(get_optional_user),
):
    """List active events from PostgreSQL. Filter: PRÓXIMO, EN CURSO, FINALIZADO."""
    if estado and estado not in VALID_ESTADOS_UI:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Estado inválido. Use: PRÓXIMO, EN CURSO o FINALIZADO",
        )

    effective_tenant = resolve_tenant_filter(current_user, tenant_id)
    items = fetch_eventos(
        db,
        tenant_id=effective_tenant,
        estado_ui=estado,
        api_base=_api_base(request),
    )
    return [EventoResponse(**item) for item in items]


@app.get("/api/eventos/resumen", response_model=EventosResumenResponse)
async def eventos_resumen(
    request: Request,
    tenant_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario | None = Depends(get_optional_user),
):
    """Summary counts for the events dashboard."""
    effective_tenant = resolve_tenant_filter(current_user, tenant_id)
    items = fetch_eventos(db, tenant_id=effective_tenant, api_base=_api_base(request))
    return EventosResumenResponse(
        total=len(items),
        en_curso=sum(1 for e in items if e["status"] == "EN CURSO"),
        proximos=sum(1 for e in items if e["status"] == "PRÓXIMO"),
        finalizados=sum(1 for e in items if e["status"] == "FINALIZADO"),
    )


@app.get("/api/eventos/{evento_id}/cronograma", response_model=EventoCronogramaResponse)
async def get_evento_cronograma(
    evento_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Usuario | None = Depends(get_optional_user),
):
    """Full competition schedule for the event calendar screen."""
    effective_tenant = resolve_tenant_filter(current_user, None)
    _ensure_event_visible(db, evento_id, effective_tenant)
    item = fetch_evento_cronograma(db, evento_id, api_base=_api_base(request))

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evento no encontrado",
        )

    return EventoCronogramaResponse(**item)


@app.get("/api/eventos/{evento_id}", response_model=EventoDetalleResponse)
async def get_evento(
    evento_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Usuario | None = Depends(get_optional_user),
):
    """Get full event details by ID."""
    effective_tenant = resolve_tenant_filter(current_user, None)
    _ensure_event_visible(db, evento_id, effective_tenant)
    item = fetch_evento_detalle(db, evento_id, api_base=_api_base(request))

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evento no encontrado",
        )

    return EventoDetalleResponse(**item)


@app.get("/api/competencias/{evento_prueba_id}", response_model=CompetenciaDetalleResponse)
async def get_competencia_detalle(evento_prueba_id: int, db: Session = Depends(get_db)):
    """Get schedule/test detail by evento_pruebas ID."""
    item = fetch_competencia_detalle(db, evento_prueba_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Competencia no encontrada",
        )
    return CompetenciaDetalleResponse(**item)


VALID_RESULT_FILTERS = {"TODOS", "RECIENTES", "DESTACADOS", "RÉCORDS", "RECORDS"}


@app.get("/api/resultados/", response_model=ResultadosIndexResponse)
async def list_resultados(
    request: Request,
    evento_id: int | None = None,
    filtro: str | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario | None = Depends(get_optional_user),
):
    """Competitions with official results and optional filtered podium list."""
    if filtro and filtro.upper() not in VALID_RESULT_FILTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filtro inválido. Use: TODOS, RECIENTES, DESTACADOS o RÉCORDS",
        )

    effective_tenant = resolve_tenant_filter(current_user, None)
    api_base = _api_base(request)
    competitions = fetch_competiciones_con_resultados(db, tenant_id=effective_tenant)
    summary = fetch_resultados_resumen(db, evento_id=evento_id, tenant_id=effective_tenant)

    results = []
    if evento_id is not None:
        _ensure_event_visible(db, evento_id, effective_tenant)
        results = fetch_resultados_por_evento(
            db, evento_id, filtro=filtro, api_base=api_base, tenant_id=effective_tenant
        )
    elif competitions:
        first_id = int(competitions[0]["id"])
        results = fetch_resultados_por_evento(
            db, first_id, filtro=filtro, api_base=api_base, tenant_id=effective_tenant
        )
        summary = fetch_resultados_resumen(db, evento_id=first_id, tenant_id=effective_tenant)

    return ResultadosIndexResponse(
        competitions=competitions,
        summary=summary,
        results=results,
    )


@app.get("/api/resultados/detalle/{result_id}", response_model=ResultadoDetalleResponse)
async def get_resultado_detalle(result_id: str, request: Request, db: Session = Depends(get_db)):
    """Full result detail for a specific test in an event."""
    item = fetch_resultado_detalle(db, result_id, api_base=_api_base(request))
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esta competición aún no tiene resultados publicados",
        )
    return ResultadoDetalleResponse(**item)


@app.post("/api/resultados/notificar", response_model=ResultadosNotificarResponse)
async def notificar_resultados_manual(
    payload: ResultadosNotificarRequest,
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_admin),
):
    """Manually notify followers when official results are published (admin)."""
    stats = resultados_notificaciones_service.notificar_resultados_publicados(
        db,
        payload.evento_id,
        payload.prueba_id,
    )
    return ResultadosNotificarResponse(**stats)


@app.post(
    "/api/resultados/procesar-notificaciones",
    response_model=ProcesarNotificacionesResultadosResponse,
)
async def procesar_notificaciones_resultados(
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_admin),
):
    """Scan database for newly published results and notify followers (admin)."""
    stats = resultados_notificaciones_service.procesar_notificaciones_resultados_nuevos(db)
    return ProcesarNotificacionesResultadosResponse(**stats)


VALID_ATLETA_FILTERS = {"TODOS", "ACTIVOS", "INACTIVOS", "NUEVOS"}
VALID_DISCIPLINA_FILTERS = {"TODOS", "OLIMPICOS", "CON_ATLETAS", "CON_PRUEBAS"}


@app.get("/api/disciplinas/", response_model=DisciplinasIndexResponse)
async def list_disciplinas(
    q: str | None = None,
    filtro: str | None = None,
    db: Session = Depends(get_db),
):
    """List sports and their sub-disciplines from PostgreSQL."""
    if filtro and filtro.upper() not in VALID_DISCIPLINA_FILTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filtro inválido. Use: TODOS, OLIMPICOS, CON_ATLETAS o CON_PRUEBAS",
        )

    disciplines = fetch_disciplinas_list(db, q=q, filtro=filtro)
    summary = fetch_disciplinas_resumen(db, filtro=filtro)
    return DisciplinasIndexResponse(disciplines=disciplines, summary=summary)


@app.get("/api/disciplinas/{deporte_id}", response_model=DisciplinaDetalleResponse)
async def get_disciplina_detalle(
    deporte_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get discipline detail with athletes, events and results."""
    item = fetch_disciplina_detalle(db, deporte_id, api_base=_api_base(request))
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Disciplina no encontrada",
        )
    return DisciplinaDetalleResponse(**item)


@app.get("/api/atletas/", response_model=AtletasIndexResponse)
async def list_atletas(
    request: Request,
    q: str | None = None,
    filtro: str | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario | None = Depends(get_optional_user),
):
    """List athletes with search and status filters."""
    if filtro and filtro.upper() not in VALID_ATLETA_FILTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filtro inválido. Use: TODOS, ACTIVOS, INACTIVOS o NUEVOS",
        )

    effective_tenant = resolve_tenant_filter(current_user, None)
    athletes = fetch_atletas_list(
        db, q=q, filtro=filtro, api_base=_api_base(request), tenant_id=effective_tenant
    )
    summary = fetch_atletas_resumen(db, filtro=filtro, tenant_id=effective_tenant)
    return AtletasIndexResponse(athletes=athletes, summary=summary)


@app.get("/api/atletas/podios", response_model=list[AtletaConPodiosItem])
async def atletas_con_podios(
    evento_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario | None = Depends(get_optional_user),
):
    """List athletes that have at least one podium (pos 1-3) in official results."""
    effective_tenant = resolve_tenant_filter(current_user, None)
    if evento_id is not None:
        _ensure_event_visible(db, evento_id, effective_tenant)
    return fetch_atletas_con_podios(db, evento_id=evento_id, tenant_id=effective_tenant)


@app.get("/api/atletas/comparar", response_model=AtletaCompareResponse)
async def comparar_atletas(
    atleta_a: int,
    atleta_b: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Compare two athletes by stats and common marks."""
    if atleta_a == atleta_b:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selecciona dos atletas distintos para comparar",
        )

    item = fetch_atletas_comparacion(
        db,
        atleta_a_id=atleta_a,
        atleta_b_id=atleta_b,
        api_base=_api_base(request),
    )
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se pudo comparar los atletas seleccionados",
        )
    return AtletaCompareResponse(**item)


@app.get("/api/atletas/{deportista_id}", response_model=AtletaPerfilResponse)
async def get_atleta(
    deportista_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Usuario | None = Depends(get_optional_user),
):
    """Get athlete profile by athlete (deportista) ID."""
    effective_tenant = resolve_tenant_filter(current_user, None)
    item = fetch_atleta_perfil(
        db, deportista_id, api_base=_api_base(request), tenant_id=effective_tenant
    )
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Atleta no encontrado",
        )
    return AtletaPerfilResponse(**item)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
