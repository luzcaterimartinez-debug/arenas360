from pydantic import BaseModel, EmailStr, Field, model_validator
from datetime import datetime
from typing import Optional, List, Literal


class UsuarioBase(BaseModel):
    email: EmailStr
    primer_nombre: str = Field(..., min_length=1, max_length=60)
    segundo_nombre: Optional[str] = Field(None, max_length=60)
    primer_apellido: str = Field(..., min_length=1, max_length=60)
    segundo_apellido: Optional[str] = Field(None, max_length=60)
    telefono: Optional[str] = Field(None, max_length=30)


class UsuarioCreate(UsuarioBase):
    password: str = Field(..., min_length=8)
    tenant_id: Optional[int] = Field(None, gt=0)
    invitation_token: Optional[str] = Field(None, min_length=8, max_length=64)

    @model_validator(mode="after")
    def validate_tenant_source(self):
        has_tenant = self.tenant_id is not None
        has_invite = bool(self.invitation_token and self.invitation_token.strip())
        if has_tenant == has_invite:
            raise ValueError("Indica una organización o un código de invitación, no ambos.")
        if has_invite:
            self.invitation_token = self.invitation_token.strip()
        return self


class TenantPublicItem(BaseModel):
    id: int
    nombre: str


class TenantsPublicResponse(BaseModel):
    items: List[TenantPublicItem]


class InvitacionPreviewResponse(BaseModel):
    valid: bool
    tenant_id: int
    tenant_nombre: str
    rol: str
    email: EmailStr


class UsuarioUpdate(BaseModel):
    primer_nombre: Optional[str] = Field(None, min_length=1, max_length=60)
    segundo_nombre: Optional[str] = Field(None, max_length=60)
    primer_apellido: Optional[str] = Field(None, min_length=1, max_length=60)
    segundo_apellido: Optional[str] = Field(None, max_length=60)
    telefono: Optional[str] = Field(None, max_length=30)


class UsuarioPerfilUpdate(BaseModel):
    primer_nombre: str = Field(..., min_length=1, max_length=60)
    segundo_nombre: Optional[str] = Field(None, max_length=60)
    primer_apellido: str = Field(..., min_length=1, max_length=60)
    segundo_apellido: Optional[str] = Field(None, max_length=60)
    telefono: Optional[str] = Field(None, max_length=30)


class UsuarioResponse(UsuarioBase):
    id: int
    tenant_id: int
    rol: str
    activo: bool
    nombre_completo: str
    image: str = ""
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    usuario: UsuarioResponse


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(..., min_length=16, max_length=255)
    password: str = Field(..., min_length=8)


class PasswordResetResponse(BaseModel):
    message: str
    debug_token: Optional[str] = None


class TokenPayload(BaseModel):
    sub: int
    email: str
    rol: str
    exp: Optional[int] = None


class EventoResponse(BaseModel):
    id: str
    title: str
    date: str
    status: str
    statusColor: str
    image: str
    tests: str
    inscribed: str
    location: str

    class Config:
        from_attributes = True


class EventosResumenResponse(BaseModel):
    total: int
    en_curso: int
    proximos: int
    finalizados: int


class CronogramaPruebaItem(BaseModel):
    time: str
    event: str
    category: str
    pool: str
    heats: int = 0
    eventoPruebaId: Optional[str] = None


class CronogramaDiaItem(BaseModel):
    id: str
    day: str
    events: List[CronogramaPruebaItem]


class AtletaInscritoItem(BaseModel):
    id: str
    name: str
    club: str
    category: str
    image: str
    events: int
    specialty: str


class EventoCronogramaResponse(BaseModel):
    id: str
    eventName: str
    startDate: str
    endDate: str
    location: str
    totalPruebas: int
    totalDays: int
    schedule: List[CronogramaDiaItem]


class EventoDetalleResponse(BaseModel):
    id: str
    title: str
    image: str
    status: str
    statusColor: str
    startDate: str
    endDate: str
    location: str
    city: str
    description: str
    totalEvents: int
    totalAthletes: int
    totalCountries: int
    organizer: str
    contact: str
    email: str
    website: str
    registrationDeadline: str
    registrationFee: str
    inscribed: int
    schedule: List[CronogramaDiaItem]
    athletes: List[AtletaInscritoItem]


class CompetenciaFaseItem(BaseModel):
    id: str
    date: str
    time: str


class CompetenciaDetalleResponse(BaseModel):
    id: str
    eventoId: str
    eventoNombre: str
    prueba: str
    category: str
    date: str
    time: str
    location: str
    heats: int = 0
    phases: List[CompetenciaFaseItem] = []
    resultId: Optional[str] = None


class AtletaMedallas(BaseModel):
    gold: int = 0
    silver: int = 0
    bronze: int = 0


class AtletaStats(BaseModel):
    participations: int = 0
    podiums: int = 0
    winRate: str = "0%"
    avgTime: str = "—"
    bestRank: int = 0
    seasons: int = 0


class AtletaMarcaItem(BaseModel):
    event: str
    time: str
    date: str
    competition: str


class AtletaResultadoRecienteItem(BaseModel):
    id: str
    event: str
    position: str
    time: str
    date: str
    competition: str


class AtletaCompetenciaItem(BaseModel):
    id: str
    name: str
    location: str
    date: str
    status: str
    statusColor: str
    position: str
    events: int


class AtletaPerfilResponse(BaseModel):
    id: str
    name: str
    specialty: str
    category: str
    club: str
    image: str
    nationality: str
    birthDate: str
    medals: AtletaMedallas = AtletaMedallas()
    records: int = 0
    stats: AtletaStats = AtletaStats()
    bestTimes: List[AtletaMarcaItem] = []
    recentResults: List[AtletaResultadoRecienteItem] = []
    competitions: List[AtletaCompetenciaItem] = []


class PodiumMemberItem(BaseModel):
    position: int
    athlete: str
    club: str
    time: str
    image: str
    isRecord: bool = False
    recordType: Optional[str] = None
    deportistaId: Optional[str] = None


class ResultadoItem(BaseModel):
    id: str
    competitionId: str
    event: str
    category: str
    podium: List[PodiumMemberItem]
    participants: int = 0
    records: int = 0
    hasFullPodium: bool = False
    hasRecords: bool = False
    isRecent: bool = False


class CompeticionResultadosItem(BaseModel):
    id: str
    name: str
    date: str
    location: str
    totalResults: int = 0


class ResultadosResumenResponse(BaseModel):
    eventos: int
    records: int
    participantes: int
    pruebas: int = 0


class ResultadosIndexResponse(BaseModel):
    competitions: List[CompeticionResultadosItem]
    summary: ResultadosResumenResponse
    results: List[ResultadoItem] = []


class ResultadoDetalleResponse(BaseModel):
    id: str
    competitionId: str
    competitionName: str
    event: str
    category: str
    date: str
    venue: str
    description: str
    participants: int
    records: int
    podium: List[PodiumMemberItem]
    ranking: List[PodiumMemberItem]
    bestMark: str
    wind: Optional[str] = None
    startDate: str = ""
    endDate: str = ""


class AtletaConPodiosItem(BaseModel):
    deportistaId: str
    athlete: str
    club: str
    podios: int
    oros: int = 0
    platas: int = 0
    bronces: int = 0


class AtletaListItem(BaseModel):
    id: str
    name: str
    specialty: str
    category: str
    categoryColor: str
    image: str
    medals: str
    records: str
    status: str
    statusColor: str
    bestTime: str
    club: str
    rating: str
    isNew: bool = False


class AtletasResumenResponse(BaseModel):
    total: int
    totalMedals: int
    totalRecords: int


class AtletaCompareMini(BaseModel):
    id: str
    name: str
    specialty: str
    club: str
    image: str
    category: str


class AtletaCompareRow(BaseModel):
    label: str
    valueA: str
    valueB: str
    leader: Optional[str] = None


class AtletaCompareMarkRow(BaseModel):
    event: str
    timeA: str
    timeB: str


class AtletaCompareResponse(BaseModel):
    athleteA: AtletaCompareMini
    athleteB: AtletaCompareMini
    rows: List[AtletaCompareRow]
    commonMarks: List[AtletaCompareMarkRow] = []


class AtletasIndexResponse(BaseModel):
    athletes: List[AtletaListItem]
    summary: AtletasResumenResponse


class DisciplinaSubItem(BaseModel):
    id: str
    name: str


class DisciplinaListItem(BaseModel):
    id: str
    name: str
    description: str
    isOlympic: bool = True
    subdisciplineCount: int = 0
    athleteCount: int = 0
    testCount: int = 0
    subdisciplines: List[DisciplinaSubItem] = []


class DisciplinasResumenResponse(BaseModel):
    totalDisciplines: int
    totalSports: int
    totalAthletes: int
    totalTests: int


class DisciplinasIndexResponse(BaseModel):
    disciplines: List[DisciplinaListItem]
    summary: DisciplinasResumenResponse


class DisciplinaDetalleSummary(BaseModel):
    athleteCount: int
    eventCount: int
    resultCount: int
    testCount: int


class DisciplinaDetalleResponse(BaseModel):
    id: str
    name: str
    description: str
    isOlympic: bool = True
    subdisciplines: List[DisciplinaSubItem] = []
    summary: DisciplinaDetalleSummary
    athletes: List[AtletaListItem] = []
    events: List[EventoResponse] = []
    results: List[ResultadoItem] = []


class SeguimientoToggleRequest(BaseModel):
    tipo: Literal["ATLETA", "EVENTO", "PRUEBA"]
    entidad_id: int = Field(..., gt=0)


class SeguimientoEstadoResponse(BaseModel):
    following: bool


class SeguimientoEstadosResponse(BaseModel):
    estados: dict[str, bool]


class SeguimientoEventoItem(EventoResponse):
    followedAt: str


class SeguimientoAtletaItem(AtletaListItem):
    followedAt: str


class SeguimientoPruebaItem(BaseModel):
    id: str
    eventoId: str
    title: str
    subtitle: str
    category: str
    date: str
    time: str = "—"
    followedAt: str


class MisSeguidosResumen(BaseModel):
    total: int
    eventos: int
    atletas: int
    pruebas: int


class MisSeguidosResponse(BaseModel):
    eventos: List[SeguimientoEventoItem]
    atletas: List[SeguimientoAtletaItem]
    pruebas: List[SeguimientoPruebaItem]
    resumen: MisSeguidosResumen


class NotificacionResponse(BaseModel):
    id: str
    title: str
    description: str
    type: str
    date: str
    time: str
    read: bool
    icon: str
    color: str
    payload: Optional[dict] = None


class NotificacionesResumenResponse(BaseModel):
    no_leidas: int


class NotificacionesListResponse(BaseModel):
    items: List[NotificacionResponse]
    resumen: NotificacionesResumenResponse


class UsuarioPreferenciasResponse(BaseModel):
    notificaciones_push: bool


class UsuarioPreferenciasUpdate(BaseModel):
    notificaciones_push: bool


class DispositivoPushRegisterRequest(BaseModel):
    push_token: str = Field(..., min_length=10, max_length=512)
    platform: Optional[str] = Field(None, max_length=20)


class ResultadosNotificarRequest(BaseModel):
    evento_id: int = Field(..., gt=0)
    prueba_id: int = Field(..., gt=0)


class ResultadosNotificarResponse(BaseModel):
    usuarios_notificados: int
    push_enviados: int


class ProcesarNotificacionesResultadosResponse(BaseModel):
    procesadas: int
    usuarios_notificados: int
    push_enviados: int


class PushPruebaResponse(BaseModel):
    push_enviados: int
    mensaje: str
