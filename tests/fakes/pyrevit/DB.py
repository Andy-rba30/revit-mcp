# -*- coding: utf-8 -*-
"""Simulacion minima de Autodesk.Revit.DB para las pruebas en CPython.

Fase 1: ElementId, XYZ, Transaction, StorageType, unos BuiltInParameter y
BuiltInCategory y un FilteredElementCollector.

Fase 2a: los elementos viven en `doc.elementos` (dict id -> elemento) y
FilteredElementCollector los recorre aplicando OfClass, OfCategory,
WhereElementIs[Not]ElementType y WherePasses. Los filtros simulados
(ElementParameterFilter con sus reglas, BoundingBoxIntersectsFilter,
ElementLevelFilter, ElementWorksetFilter, ElementMulticategoryFilter) exponen
`pasa(elemento)` con la semantica que se espera de Revit. Las clases de
elemento de aqui solo aportan lo que leen escritura.describir_elemento y los
isinstance() de los manejadores; los elementos con comportamiento
(parametros, anfitrion, uniones, vistas...) estan en tests/fakes/modelo_falso.py.
Las fabricas estaticas (Grid.Create, Level.Create, ViewSheet.Create,
Viewport.Create, ScheduleSheetInstance.Create, Toposolid.Create) registran el
elemento nuevo en el documento con doc.agregar(elemento).
"""


class _Enum(object):
    def __init__(self, nombre, valor=None):
        self.nombre = nombre
        self.valor = valor

    def __repr__(self):
        return self.nombre

    __str__ = __repr__

    def __int__(self):
        return int(self.valor if self.valor is not None else 0)


class TransactionStatus(object):
    Uninitialized = _Enum("Uninitialized")
    Started = _Enum("Started")
    Committed = _Enum("Committed")
    RolledBack = _Enum("RolledBack")
    Error = _Enum("Error")


class FailureSeverity(object):
    None_ = _Enum("None")
    Warning = _Enum("Warning")
    Error = _Enum("Error")


class FailureProcessingResult(object):
    Continue = _Enum("Continue")
    ProceedWithCommit = _Enum("ProceedWithCommit")
    ProceedWithRollBack = _Enum("ProceedWithRollBack")


class IFailuresPreprocessor(object):
    pass


class ElementId(object):
    """Acepta un entero, otro ElementId o un _Enum (BuiltInParameter / BuiltInCategory)."""

    def __init__(self, value=-1):
        self.bip = None
        if isinstance(value, ElementId):
            self.bip = value.bip
            value = value.Value
        elif isinstance(value, _Enum):
            self.bip = value
            value = value.valor if value.valor is not None else -1
        self.Value = int(value)
        self.IntegerValue = int(value)

    def __eq__(self, other):
        return isinstance(other, ElementId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)

    def __repr__(self):
        return "ElementId({})".format(self.Value)


ElementId.InvalidElementId = ElementId(-1)


class XYZ(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)

    def DistanceTo(self, otro):
        return ((self.X - otro.X) ** 2 + (self.Y - otro.Y) ** 2 + (self.Z - otro.Z) ** 2) ** 0.5

    def Add(self, otro):
        return XYZ(self.X + otro.X, self.Y + otro.Y, self.Z + otro.Z)

    def Subtract(self, otro):
        return XYZ(self.X - otro.X, self.Y - otro.Y, self.Z - otro.Z)

    def Multiply(self, factor):
        return XYZ(self.X * factor, self.Y * factor, self.Z * factor)

    def GetLength(self):
        return (self.X ** 2 + self.Y ** 2 + self.Z ** 2) ** 0.5

    def Normalize(self):
        longitud = self.GetLength() or 1.0
        return XYZ(self.X / longitud, self.Y / longitud, self.Z / longitud)

    def DotProduct(self, otro):
        return self.X * otro.X + self.Y * otro.Y + self.Z * otro.Z

    def CrossProduct(self, otro):
        return XYZ(
            self.Y * otro.Z - self.Z * otro.Y,
            self.Z * otro.X - self.X * otro.Z,
            self.X * otro.Y - self.Y * otro.X,
        )

    def IsAlmostEqualTo(self, otro, tolerancia=1e-9):
        return self.DistanceTo(otro) <= tolerancia

    def __repr__(self):
        return "XYZ({:.4f}, {:.4f}, {:.4f})".format(self.X, self.Y, self.Z)


XYZ.Zero = XYZ(0, 0, 0)
XYZ.BasisX = XYZ(1, 0, 0)
XYZ.BasisY = XYZ(0, 1, 0)
XYZ.BasisZ = XYZ(0, 0, 1)


class UV(object):
    def __init__(self, u=0.0, v=0.0):
        self.U = float(u)
        self.V = float(v)


class Line(object):
    def __init__(self, a=None, b=None):
        self.a = a or XYZ()
        self.b = b or XYZ()
        self.IsBound = True

    @staticmethod
    def CreateBound(a, b):
        return Line(a, b)

    def GetEndPoint(self, indice):
        return self.a if indice == 0 else self.b

    @property
    def Length(self):
        return self.a.DistanceTo(self.b)

    @property
    def Direction(self):
        return self.b.Subtract(self.a).Normalize()

    def Evaluate(self, parametro, normalizado=True):
        t = parametro if normalizado else (parametro / (self.Length or 1.0))
        return self.a.Add(self.b.Subtract(self.a).Multiply(t))

    def Distance(self, punto):
        """Distancia del punto al segmento."""
        d = self.b.Subtract(self.a)
        longitud2 = d.DotProduct(d)
        if longitud2 <= 0:
            return punto.DistanceTo(self.a)
        t = max(0.0, min(1.0, punto.Subtract(self.a).DotProduct(d) / longitud2))
        return punto.DistanceTo(self.a.Add(d.Multiply(t)))


class Arc(object):
    pass


class CurveLoop(object):
    def __init__(self):
        self.curvas = []

    def Append(self, curva):
        self.curvas.append(curva)

    def __iter__(self):
        return iter(self.curvas)


class CurveArray(CurveLoop):
    pass


class Outline(object):
    def __init__(self, minimo, maximo):
        self.MinimumPoint = minimo
        self.MaximumPoint = maximo


class Transform(object):
    def __init__(self):
        self.Origin = XYZ(0, 0, 0)
        self.BasisX = XYZ(1, 0, 0)
        self.BasisY = XYZ(0, 1, 0)
        self.BasisZ = XYZ(0, 0, 1)

    @property
    def IsIdentity(self):
        return self.Origin.GetLength() < 1e-9

    def OfPoint(self, punto):
        return XYZ(
            self.Origin.X + self.BasisX.X * punto.X + self.BasisY.X * punto.Y + self.BasisZ.X * punto.Z,
            self.Origin.Y + self.BasisX.Y * punto.X + self.BasisY.Y * punto.Y + self.BasisZ.Y * punto.Z,
            self.Origin.Z + self.BasisX.Z * punto.X + self.BasisY.Z * punto.Y + self.BasisZ.Z * punto.Z,
        )


Transform.Identity = Transform()


class BoundingBoxXYZ(object):
    def __init__(self, minimo=None, maximo=None):
        self.Min = minimo or XYZ()
        self.Max = maximo or XYZ()
        self.Transform = Transform()
        self.Enabled = True


class Category(object):
    def __init__(self, nombre, bic=None, tipo=None):
        self.Name = nombre
        self.BuiltInCategory = bic
        self.Id = ElementId(bic) if bic is not None else ElementId(-1)
        self.CategoryType = tipo if tipo is not None else CategoryType.Model
        self.HasMaterialQuantities = True


class BuiltInParameter(object):
    """Atributos de ejemplo; getattr sobre nombres desconocidos falla como en Revit."""
    INVALID = _Enum("INVALID", -1)
    FAMILY_LEVEL_PARAM = _Enum("FAMILY_LEVEL_PARAM", -1001200)
    LEVEL_PARAM = _Enum("LEVEL_PARAM", -1001201)
    SCHEDULE_LEVEL_PARAM = _Enum("SCHEDULE_LEVEL_PARAM", -1001202)
    FAMILY_BASE_LEVEL_PARAM = _Enum("FAMILY_BASE_LEVEL_PARAM", -1001203)
    ELEM_PARTITION_PARAM = _Enum("ELEM_PARTITION_PARAM", -1001204)
    ALL_MODEL_INSTANCE_COMMENTS = _Enum("ALL_MODEL_INSTANCE_COMMENTS", -1001205)
    WALL_USER_HEIGHT_PARAM = _Enum("WALL_USER_HEIGHT_PARAM", -1001206)
    ALL_MODEL_MARK = _Enum("ALL_MODEL_MARK", -1001207)
    ALL_MODEL_TYPE_COMMENTS = _Enum("ALL_MODEL_TYPE_COMMENTS", -1001208)
    ELEM_TYPE_PARAM = _Enum("ELEM_TYPE_PARAM", -1001209)
    PHASE_CREATED = _Enum("PHASE_CREATED", -1001210)
    PHASE_DEMOLISHED = _Enum("PHASE_DEMOLISHED", -1001211)
    VIEWER_SHEET_NUMBER = _Enum("VIEWER_SHEET_NUMBER", -1001212)
    VIEW_PHASE = _Enum("VIEW_PHASE", -1001213)
    CURVE_ELEM_LENGTH = _Enum("CURVE_ELEM_LENGTH", -1001214)
    HOST_AREA_COMPUTED = _Enum("HOST_AREA_COMPUTED", -1001215)
    HOST_VOLUME_COMPUTED = _Enum("HOST_VOLUME_COMPUTED", -1001216)
    ROOM_NAME = _Enum("ROOM_NAME", -1001217)
    ROOM_NUMBER = _Enum("ROOM_NUMBER", -1001218)
    WALL_BASE_OFFSET = _Enum("WALL_BASE_OFFSET", -1001219)
    FLOOR_HEIGHTABOVELEVEL_PARAM = _Enum("FLOOR_HEIGHTABOVELEVEL_PARAM", -1001220)
    FAMILY_TOP_LEVEL_PARAM = _Enum("FAMILY_TOP_LEVEL_PARAM", -1001221)
    FAMILY_TOP_LEVEL_OFFSET_PARAM = _Enum("FAMILY_TOP_LEVEL_OFFSET_PARAM", -1001222)
    ALL_MODEL_TYPE_NAME = _Enum("ALL_MODEL_TYPE_NAME", -1001223)


class BuiltInCategory(object):
    OST_Walls = _Enum("OST_Walls", -2000011)
    OST_Levels = _Enum("OST_Levels", -2000240)
    OST_Doors = _Enum("OST_Doors", -2000023)
    OST_Windows = _Enum("OST_Windows", -2000014)
    OST_Grids = _Enum("OST_Grids", -2000220)
    OST_Floors = _Enum("OST_Floors", -2000032)
    OST_Roofs = _Enum("OST_Roofs", -2000035)
    OST_Ceilings = _Enum("OST_Ceilings", -2000038)
    OST_StructuralFraming = _Enum("OST_StructuralFraming", -2001320)
    OST_StructuralColumns = _Enum("OST_StructuralColumns", -2001330)
    OST_StructuralFoundation = _Enum("OST_StructuralFoundation", -2001300)
    OST_Rooms = _Enum("OST_Rooms", -2000160)
    OST_Sheets = _Enum("OST_Sheets", -2003100)
    OST_TitleBlocks = _Enum("OST_TitleBlocks", -2000280)
    OST_Views = _Enum("OST_Views", -2000279)
    OST_Dimensions = _Enum("OST_Dimensions", -2000170)
    OST_WallTags = _Enum("OST_WallTags", -2000013)
    OST_DoorTags = _Enum("OST_DoorTags", -2000460)
    OST_Topography = _Enum("OST_Topography", -2001260)
    OST_Toposolid = _Enum("OST_Toposolid", -2001261)
    OST_GenericModel = _Enum("OST_GenericModel", -2000151)
    OST_Furniture = _Enum("OST_Furniture", -2000080)
    OST_Columns = _Enum("OST_Columns", -2000100)
    OST_DuctCurves = _Enum("OST_DuctCurves", -2008000)
    OST_PipeCurves = _Enum("OST_PipeCurves", -2008044)
    OST_Lines = _Enum("OST_Lines", -2000051)
    OST_ProjectBasePoint = _Enum("OST_ProjectBasePoint", -2001267)
    OST_SharedBasePoint = _Enum("OST_SharedBasePoint", -2001268)


class CategoryType(object):
    Model = _Enum("Model", 1)
    Annotation = _Enum("Annotation", 2)
    Internal = _Enum("Internal", 3)
    AnalyticalModel = _Enum("AnalyticalModel", 4)


class ViewType(object):
    Undefined = _Enum("Undefined")
    FloorPlan = _Enum("FloorPlan")
    CeilingPlan = _Enum("CeilingPlan")
    Elevation = _Enum("Elevation")
    ThreeD = _Enum("ThreeD")
    Schedule = _Enum("Schedule")
    DrawingSheet = _Enum("DrawingSheet")
    ProjectBrowser = _Enum("ProjectBrowser")
    Report = _Enum("Report")
    DraftingView = _Enum("DraftingView")
    Legend = _Enum("Legend")
    SystemBrowser = _Enum("SystemBrowser")
    EngineeringPlan = _Enum("EngineeringPlan")
    AreaPlan = _Enum("AreaPlan")
    Section = _Enum("Section")
    Detail = _Enum("Detail")
    Internal = _Enum("Internal")


class ViewDiscipline(object):
    Architectural = _Enum("Architectural")
    Structural = _Enum("Structural")
    Mechanical = _Enum("Mechanical")
    Electrical = _Enum("Electrical")
    Plumbing = _Enum("Plumbing")
    Coordination = _Enum("Coordination")


class ViewDetailLevel(object):
    Undefined = _Enum("Undefined")
    Coarse = _Enum("Coarse")
    Medium = _Enum("Medium")
    Fine = _Enum("Fine")


class ViewFamily(object):
    FloorPlan = _Enum("FloorPlan")
    CeilingPlan = _Enum("CeilingPlan")
    Section = _Enum("Section")
    Elevation = _Enum("Elevation")
    ThreeDimensional = _Enum("ThreeDimensional")
    Schedule = _Enum("Schedule")
    Sheet = _Enum("Sheet")


class SectionType(object):
    None_ = _Enum("None")
    Header = _Enum("Header")
    Body = _Enum("Body")
    Summary = _Enum("Summary")
    Footer = _Enum("Footer")


class PlanViewPlane(object):
    TopClipPlane = _Enum("TopClipPlane")
    CutPlane = _Enum("CutPlane")
    BottomClipPlane = _Enum("BottomClipPlane")
    ViewDepthPlane = _Enum("ViewDepthPlane")
    UnderlayBottom = _Enum("UnderlayBottom")


class PlanViewRange(object):
    """Rango de vista: constantes de clase y, como instancia, niveles y desfases por plano."""
    Unlimited = ElementId(-3)
    Current = ElementId(-4)
    LevelAbove = ElementId(-5)
    LevelBelow = ElementId(-6)

    def __init__(self, niveles=None, desfases=None):
        self.niveles = niveles or {}
        self.desfases = desfases or {}

    def GetLevelId(self, plano):
        return self.niveles.get(plano, PlanViewRange.Current)

    def GetOffset(self, plano):
        return self.desfases.get(plano, 0.0)


class ImportPlacement(object):
    Centered = _Enum("Centered")
    Origin = _Enum("Origin")
    Shared = _Enum("Shared")
    Site = _Enum("Site")


class WorksetKind(object):
    UserWorkset = _Enum("UserWorkset")
    FamilyWorkset = _Enum("FamilyWorkset")
    ViewWorkset = _Enum("ViewWorkset")
    StandardWorkset = _Enum("StandardWorkset")


class StorageType(object):
    None_ = _Enum("None")
    Integer = _Enum("Integer")
    Double = _Enum("Double")
    String = _Enum("String")
    ElementId = _Enum("ElementId")


# ---------------------------------------------------------------------------
# Fallos (advertencias) y sus identificadores
# ---------------------------------------------------------------------------
class FailureDefinitionId(object):
    def __init__(self, guid):
        self.Guid = guid

    def __repr__(self):
        return "FailureDefinitionId({})".format(self.Guid)


class BuiltInFailures(object):
    class OverlapFailures(object):
        WallsOverlap = FailureDefinitionId("b4176a2e-0000-4000-8000-walls-overlap")
        DuplicateInstances = FailureDefinitionId("b4176a2e-0000-4000-8000-duplicate-inst")
        WallRoomSeparationOverlap = FailureDefinitionId("b4176a2e-0000-4000-8000-wall-roomsep")
        RoomSeparationLinesOverlap = FailureDefinitionId("b4176a2e-0000-4000-8000-roomsep-overlap")
        FloorsOverlap = FailureDefinitionId("b4176a2e-0000-4000-8000-floors-overlap")

    class RoomFailures(object):
        RoomNotEnclosed = FailureDefinitionId("b4176a2e-0000-4000-8000-room-not-enclosed")
        RoomNotInPlaced = FailureDefinitionId("b4176a2e-0000-4000-8000-room-not-placed")
        RoomsInSameRegion = FailureDefinitionId("b4176a2e-0000-4000-8000-rooms-same-region")

    class JoinElementsFailures(object):
        CannotKeepJoined = FailureDefinitionId("b4176a2e-0000-4000-8000-cannot-keep-joined")
        CannotKeepJoinedWarning = FailureDefinitionId("b4176a2e-0000-4000-8000-cannot-keep-joined-w")

    class InaccurateFailures(object):
        InaccurateLine = FailureDefinitionId("b4176a2e-0000-4000-8000-inaccurate-line")
        InaccurateWall = FailureDefinitionId("b4176a2e-0000-4000-8000-inaccurate-wall")
        InaccurateBeamOrBrace = FailureDefinitionId("b4176a2e-0000-4000-8000-inaccurate-beam")
        InaccurateGrid = FailureDefinitionId("b4176a2e-0000-4000-8000-inaccurate-grid")
        InaccurateRefPlane = FailureDefinitionId("b4176a2e-0000-4000-8000-inaccurate-refplane")

    class GeneralFailures(object):
        DuplicateValue = FailureDefinitionId("b4176a2e-0000-4000-8000-duplicate-value")

    class WallFailures(object):
        WallNotAttached = FailureDefinitionId("b4176a2e-0000-4000-8000-wall-not-attached")

    class AreaFailures(object):
        AreaNotEnclosed = FailureDefinitionId("b4176a2e-0000-4000-8000-area-not-enclosed")


# ---------------------------------------------------------------------------
# Transacciones
# ---------------------------------------------------------------------------
class _Opciones(object):
    def SetForcedModalHandling(self, valor):
        self.modal = valor

    def SetClearAfterRollback(self, valor):
        self.clear = valor

    def SetFailuresPreprocessor(self, pre):
        self.pre = pre


class Transaction(object):
    """Registra Start/Commit/RollBack. `resultado_commit` permite simular un fallo."""

    creadas = []
    resultado_commit = TransactionStatus.Committed

    def __init__(self, doc, nombre):
        self.doc = doc
        self.nombre = nombre
        self.iniciada = False
        self.terminada = False
        self.estado = TransactionStatus.Uninitialized
        self.opciones = _Opciones()
        Transaction.creadas.append(self)
        if doc is not None and hasattr(doc, "transacciones"):
            doc.transacciones.append(self)

    def Start(self):
        self.iniciada = True
        self.estado = TransactionStatus.Started
        if self.doc is not None and hasattr(self.doc, "IsModifiable"):
            self.doc.IsModifiable = True
        return self.estado

    def Commit(self):
        self.terminada = True
        self.estado = Transaction.resultado_commit
        if self.doc is not None and hasattr(self.doc, "IsModifiable"):
            self.doc.IsModifiable = False
        return self.estado

    def RollBack(self):
        self.terminada = True
        self.estado = TransactionStatus.RolledBack
        if self.doc is not None and hasattr(self.doc, "IsModifiable"):
            self.doc.IsModifiable = False
        return self.estado

    def HasStarted(self):
        return self.iniciada

    def HasEnded(self):
        return self.terminada

    def GetName(self):
        return self.nombre

    def GetFailureHandlingOptions(self):
        return self.opciones

    def SetFailureHandlingOptions(self, opciones):
        self.opciones = opciones


class TransactionGroup(Transaction):
    def Assimilate(self):
        self.terminada = True
        self.estado = TransactionStatus.Committed
        return self.estado


# ---------------------------------------------------------------------------
# Elementos
# ---------------------------------------------------------------------------
class _TipoNet(object):
    def __init__(self, nombre):
        self.Name = nombre


class Element(object):
    """Base con lo que leen describir_elemento / nombre_nivel / bbox_mm.

    Las subclases de aqui no definen __init__ propio: modelo_falso.Elemento
    hereda de Element y de la clase de DB que toque (Wall, Level, ViewPlan...)."""

    def __init__(self):
        self.Id = ElementId.InvalidElementId
        self.Name = None
        self.Category = None
        self.LevelId = ElementId.InvalidElementId
        self.UniqueId = None
        self.Pinned = False
        self.ViewSpecific = False
        self.OwnerViewId = ElementId.InvalidElementId
        self.Parameters = []
        self.Location = None
        self.bic = None
        self.type_id = ElementId.InvalidElementId
        self.caja = None

    def GetTypeId(self):
        return self.type_id if self.type_id is not None else ElementId.InvalidElementId

    def get_Parameter(self, bip):
        for parametro in self.Parameters:
            if getattr(parametro, "bip", None) is bip:
                return parametro
        return None

    def LookupParameter(self, nombre):
        for parametro in self.Parameters:
            try:
                if parametro.Definition.Name == nombre:
                    return parametro
            except Exception:
                continue
        return None

    def GetOrderedParameters(self):
        return list(self.Parameters)

    def get_BoundingBox(self, vista):
        return self.caja

    def GetDependentElements(self, filtro):
        return [self.Id] + [ElementId(i) for i in getattr(self, "dependientes", [])]

    def GetType(self):
        return _TipoNet(type(self).__name__)


class ElementType(Element):
    pass


class FamilySymbol(ElementType):
    IsActive = True

    def Activate(self):
        self.IsActive = True


class WallType(ElementType):
    pass


class FloorType(ElementType):
    pass


class RoofType(ElementType):
    pass


class ToposolidType(ElementType):
    pass


class ViewFamilyType(ElementType):
    pass


class Family(Element):
    pass


class FamilyInstance(Element):
    Host = None
    SuperComponent = None


class HostObject(Element):
    def FindInserts(self, huecos, sombras, muros_embebidos, compartidos):
        return [ElementId(i) for i in getattr(self, "insertos", [])]


class Wall(HostObject):
    pass


class Floor(HostObject):
    pass


class RoofBase(HostObject):
    pass


class Ceiling(HostObject):
    pass


class Toposolid(Element):
    @staticmethod
    def Create(doc, *args):
        """Create(doc, puntos, typeId, levelId) o Create(doc, loops, puntos, typeId, levelId)."""
        topo = Toposolid()
        if len(args) == 4:
            topo.contorno, puntos, tipo_id, nivel_id = args
        else:
            puntos, tipo_id, nivel_id = args
            topo.contorno = None
        topo.puntos = list(puntos)
        topo.type_id = tipo_id
        topo.LevelId = nivel_id
        topo.bic = BuiltInCategory.OST_Toposolid
        topo.Category = Category("Toposólido", BuiltInCategory.OST_Toposolid)
        doc.agregar(topo)
        return topo


class Level(Element):
    # Como en Revit: Create y ProjectElevation trabajan respecto al origen interno;
    # Elevation es la mostrada, que depende de la Base de elevacion del tipo. Las
    # pruebas simulan un punto base desplazado con Level.desfase_base (pies).
    desfase_base = 0.0
    _interna = 0.0

    @property
    def ProjectElevation(self):
        return self._interna

    @property
    def Elevation(self):
        return self._interna + Level.desfase_base

    @Elevation.setter
    def Elevation(self, valor):
        self._interna = float(valor) - Level.desfase_base

    @staticmethod
    def Create(doc, elevacion):
        nivel = Level()
        nivel._interna = float(elevacion)
        nivel.bic = BuiltInCategory.OST_Levels
        nivel.Category = Category("Niveles", BuiltInCategory.OST_Levels, CategoryType.Annotation)
        doc.agregar(nivel)
        nivel.Name = u"Nivel {}".format(nivel.Id.Value)
        return nivel


class Grid(Element):
    @staticmethod
    def Create(doc, curva):
        rejilla = Grid()
        rejilla.Curve = curva
        rejilla.bic = BuiltInCategory.OST_Grids
        rejilla.Category = Category("Rejillas", BuiltInCategory.OST_Grids, CategoryType.Annotation)
        doc.agregar(rejilla)
        rejilla.Name = u"Rejilla {}".format(rejilla.Id.Value)
        return rejilla


class View(Element):
    IsTemplate = False
    ViewType = ViewType.Undefined
    Scale = 100
    CropBoxActive = False
    CropBoxVisible = False
    CropBox = None
    Discipline = ViewDiscipline.Architectural
    DetailLevel = ViewDetailLevel.Medium
    ViewTemplateId = ElementId.InvalidElementId
    GenLevel = None


class ViewPlan(View):
    ViewType = ViewType.FloorPlan

    def GetViewRange(self):
        return getattr(self, "rango", PlanViewRange())


class View3D(View):
    ViewType = ViewType.ThreeD
    IsSectionBoxActive = False

    def GetSectionBox(self):
        return getattr(self, "caja_seccion", None)


class ViewSheet(View):
    ViewType = ViewType.DrawingSheet
    SheetNumber = None

    @staticmethod
    def Create(doc, cajetin_id):
        plano = ViewSheet()
        plano.bic = BuiltInCategory.OST_Sheets
        plano.Category = Category("Planos", BuiltInCategory.OST_Sheets, CategoryType.Annotation)
        plano.title_block_id = cajetin_id
        doc.agregar(plano)
        plano.SheetNumber = u"S-{}".format(plano.Id.Value)
        plano.Name = u"Unnamed"
        return plano


class ScheduleField(object):
    def __init__(self, nombre, encabezado=None, oculto=False):
        self.nombre = nombre
        self.ColumnHeading = encabezado if encabezado is not None else nombre
        self.IsHidden = oculto

    def GetName(self):
        return self.nombre


class ScheduleDefinition(object):
    def __init__(self, campos=None, mostrar_encabezados=True):
        self.campos = list(campos or [])
        self.ShowHeaders = mostrar_encabezados
        self.IsItemized = True
        self.ShowGrandTotal = False

    def GetFieldCount(self):
        return len(self.campos)

    def GetField(self, indice):
        return self.campos[indice]


class TableSectionData(object):
    def __init__(self, filas):
        self.filas = filas

    @property
    def NumberOfRows(self):
        return len(self.filas)

    @property
    def NumberOfColumns(self):
        return max([len(f) for f in self.filas] or [0])


class TableData(object):
    def __init__(self, secciones):
        self.secciones = secciones

    def GetSectionData(self, tipo):
        return TableSectionData(self.secciones.get(tipo, []))


class ViewSchedule(View):
    ViewType = ViewType.Schedule
    Definition = None
    filas = ()
    titulo = ()

    def GetTableData(self):
        return TableData({SectionType.Body: list(self.filas), SectionType.Header: list(self.titulo)})

    def GetCellText(self, seccion, fila, columna):
        filas = self.filas if seccion is SectionType.Body else self.titulo
        try:
            return filas[fila][columna]
        except (IndexError, TypeError):
            return u""


class Viewport(Element):
    ViewId = None
    SheetId = None

    @staticmethod
    def CanAddViewToSheet(doc, plano_id, vista_id):
        vista = doc.GetElement(vista_id)
        if vista is None or isinstance(vista, ViewSchedule) or getattr(vista, "IsTemplate", False):
            return False
        if getattr(vista, "ViewType", None) is ViewType.Legend:
            return True  # como en Revit: una leyenda puede estar en varios planos
        for elemento in doc.elementos.values():
            if isinstance(elemento, Viewport) and elemento.ViewId == vista_id:
                return False
        return True

    @staticmethod
    def Create(doc, plano_id, vista_id, punto):
        if not Viewport.CanAddViewToSheet(doc, plano_id, vista_id):
            raise Exception("The view cannot be added to the sheet (already placed or not placeable)")
        vp = Viewport()
        vp.ViewId = vista_id
        vp.SheetId = plano_id
        vp.punto = punto
        vp.bic = BuiltInCategory.OST_Views
        vp.Category = Category("Ventanas gráficas", BuiltInCategory.OST_Views, CategoryType.Annotation)
        doc.agregar(vp)
        return vp


class ScheduleSheetInstance(Element):
    ScheduleId = None

    @staticmethod
    def Create(doc, plano_id, tabla_id, punto):
        for elemento in doc.elementos.values():
            if isinstance(elemento, ScheduleSheetInstance) and elemento.ScheduleId == tabla_id:
                raise Exception("The schedule is already placed on a sheet")
        instancia = ScheduleSheetInstance()
        instancia.ScheduleId = tabla_id
        instancia.SheetId = plano_id
        instancia.punto = punto
        instancia.Category = Category("Gráficos de tabla", None, CategoryType.Annotation)
        doc.agregar(instancia)
        return instancia


class Reference(object):
    def __init__(self, elemento):
        self.ElementId = elemento.Id if hasattr(elemento, "Id") else elemento


class ReferenceArray(object):
    def __init__(self):
        self.refs = []

    def Append(self, ref):
        self.refs.append(ref)

    @property
    def Size(self):
        return len(self.refs)


class Dimension(Element):
    References = ()


class IndependentTag(Element):
    def GetTaggedLocalElementIds(self):
        return [ElementId(i) for i in getattr(self, "etiquetados", [])]


class ImportInstance(Element):
    IsLinked = True


class RevitLinkInstance(Element):
    pass


class Phase(Element):
    pass


class Solid(object):
    def __init__(self, volumen=0.0, area=0.0, caras=6, aristas=12, centro=None):
        self.Volume = volumen
        self.SurfaceArea = area
        self.Faces = _Coleccion(caras)
        self.Edges = _Coleccion(aristas)
        self.centro = centro or XYZ()

    def ComputeCentroid(self):
        return self.centro


class _Coleccion(object):
    def __init__(self, n):
        self.Size = n


class GeometryInstance(object):
    def __init__(self, contenido):
        self.contenido = contenido

    def GetInstanceGeometry(self):
        return list(self.contenido)


class Options(object):
    def __init__(self):
        self.DetailLevel = ViewDetailLevel.Medium
        self.ComputeReferences = False
        self.IncludeNonVisibleObjects = False
        self.View = None


class DWGImportOptions(object):
    def __init__(self):
        self.Placement = ImportPlacement.Origin
        self.ThisViewOnly = False


class DGNImportOptions(object):
    def __init__(self):
        self.Placement = ImportPlacement.Origin
        self.ThisViewOnly = False


class SATImportOptions(object):
    pass


class SKPImportOptions(object):
    pass


# ---------------------------------------------------------------------------
# Utilidades estaticas
# ---------------------------------------------------------------------------
class JoinGeometryUtils(object):
    @staticmethod
    def GetJoinedElements(doc, elemento):
        return [ElementId(i) for i in getattr(elemento, "unidos", [])]

    @staticmethod
    def AreElementsJoined(doc, a, b):
        return b.Id.Value in getattr(a, "unidos", [])

    @staticmethod
    def JoinGeometry(doc, a, b):
        a.unidos.append(b.Id.Value)
        b.unidos.append(a.Id.Value)

    @staticmethod
    def UnjoinGeometry(doc, a, b):
        a.unidos.remove(b.Id.Value)
        b.unidos.remove(a.Id.Value)

    @staticmethod
    def IsCuttingElementInJoin(doc, a, b):
        return True


class ElementTransformUtils(object):
    @staticmethod
    def MoveElement(doc, elem_id, delta):
        elemento = doc.GetElement(elem_id)
        if elemento is not None and hasattr(elemento, "mover"):
            elemento.mover(delta)

    @staticmethod
    def CopyElement(doc, elem_id, delta):
        return []


class LabelUtils(object):
    @staticmethod
    def GetLabelForGroup(group_id):
        return "Group"


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------
class ParameterValueProvider(object):
    def __init__(self, param_id):
        self.param_id = param_id


class FilterStringEquals(object):
    metodo = "="


class FilterStringContains(object):
    metodo = "contains"


class FilterStringBeginsWith(object):
    metodo = "starts"


class FilterNumericEquals(object):
    metodo = "="


class FilterNumericGreater(object):
    metodo = ">"


class FilterNumericGreaterOrEqual(object):
    metodo = ">="


class FilterNumericLess(object):
    metodo = "<"


class FilterNumericLessOrEqual(object):
    metodo = "<="


def _parametro_del_proveedor(elemento, param_id):
    bip = getattr(param_id, "bip", None)
    if bip is not None:
        try:
            return elemento.get_Parameter(bip)
        except Exception:
            return None
    for parametro in getattr(elemento, "Parameters", []):
        if getattr(parametro, "Id", None) == param_id:
            return parametro
    return None


def _compara(metodo, actual, esperado):
    if metodo == "=":
        return actual == esperado
    if metodo == ">":
        return actual > esperado
    if metodo == ">=":
        return actual >= esperado
    if metodo == "<":
        return actual < esperado
    if metodo == "<=":
        return actual <= esperado
    return False


class FilterRule(object):
    def __init__(self, provider, evaluator, value):
        self.provider = provider
        self.evaluator = evaluator
        self.value = value

    def _parametro(self, elemento):
        parametro = _parametro_del_proveedor(elemento, self.provider.param_id)
        if parametro is None or not parametro.HasValue:
            return None
        return parametro


class FilterStringRule(FilterRule):
    """Revit 2023+: FilterStringRule(provider, evaluator, value); sin distinguir mayusculas."""

    def __init__(self, provider, evaluator, value, case_sensitive=None):
        FilterRule.__init__(self, provider, evaluator, value)
        if case_sensitive is not None:
            raise TypeError("FilterStringRule(provider, evaluator, value): el argumento caseSensitive no existe en 2023+")

    def pasa(self, elemento):
        parametro = self._parametro(elemento)
        if parametro is None or parametro.StorageType is not StorageType.String:
            return False
        actual = (parametro.AsString() or u"").lower()
        esperado = (self.value or u"").lower()
        metodo = self.evaluator.metodo
        if metodo == "=":
            return actual == esperado
        if metodo == "contains":
            return esperado in actual
        if metodo == "starts":
            return actual.startswith(esperado)
        return False


class FilterDoubleRule(FilterRule):
    def __init__(self, provider, evaluator, value, epsilon):
        FilterRule.__init__(self, provider, evaluator, value)
        self.epsilon = epsilon

    def pasa(self, elemento):
        parametro = self._parametro(elemento)
        if parametro is None or parametro.StorageType is not StorageType.Double:
            return False
        actual = parametro.AsDouble()
        metodo = self.evaluator.metodo
        if metodo == "=":
            return abs(actual - self.value) <= self.epsilon
        return _compara(metodo, actual, self.value)


class FilterIntegerRule(FilterRule):
    def pasa(self, elemento):
        parametro = self._parametro(elemento)
        if parametro is None or parametro.StorageType is not StorageType.Integer:
            return False
        return _compara(self.evaluator.metodo, parametro.AsInteger(), self.value)


class FilterElementIdRule(FilterRule):
    def pasa(self, elemento):
        parametro = self._parametro(elemento)
        if parametro is None or parametro.StorageType is not StorageType.ElementId:
            return False
        return _compara(self.evaluator.metodo, parametro.AsElementId().Value, self.value.Value)


class ElementParameterFilter(object):
    def __init__(self, regla, invertido=False):
        self.regla = regla
        self.invertido = invertido

    def pasa(self, elemento):
        resultado = bool(self.regla.pasa(elemento))
        return (not resultado) if self.invertido else resultado


class BoundingBoxIntersectsFilter(object):
    def __init__(self, contorno, invertido=False):
        self.contorno = contorno
        self.invertido = invertido

    def pasa(self, elemento):
        try:
            caja = elemento.get_BoundingBox(None)
        except Exception:
            caja = None
        if caja is None:
            return False
        a, b = self.contorno.MinimumPoint, self.contorno.MaximumPoint
        cruza = (
            caja.Min.X <= b.X and caja.Max.X >= a.X
            and caja.Min.Y <= b.Y and caja.Max.Y >= a.Y
            and caja.Min.Z <= b.Z and caja.Max.Z >= a.Z
        )
        return (not cruza) if self.invertido else cruza


class ElementLevelFilter(object):
    def __init__(self, nivel_id, invertido=False):
        self.nivel_id = nivel_id
        self.invertido = invertido

    def pasa(self, elemento):
        coincide = getattr(elemento, "LevelId", None) == self.nivel_id
        return (not coincide) if self.invertido else coincide


class ElementWorksetFilter(object):
    def __init__(self, workset_id, invertido=False):
        self.workset_id = workset_id
        self.invertido = invertido

    def pasa(self, elemento):
        propio = getattr(elemento, "WorksetId", None)
        coincide = propio is not None and int(getattr(propio, "IntegerValue", propio)) == int(
            getattr(self.workset_id, "IntegerValue", self.workset_id)
        )
        return (not coincide) if self.invertido else coincide


class ElementMulticategoryFilter(object):
    def __init__(self, categorias, invertido=False):
        self.categorias = list(categorias)
        self.invertido = invertido

    def pasa(self, elemento):
        dentro = getattr(elemento, "bic", None) in self.categorias
        return (not dentro) if self.invertido else dentro


class ElementIntersectsElementFilter(object):
    def __init__(self, elemento):
        self.elemento = elemento

    def pasa(self, elemento):
        return False


class FilteredElementCollector(object):
    """Recorre `doc.elementos` (dict id -> elemento) aplicando los criterios en orden."""

    def __init__(self, doc, view_id=None):
        self.doc = doc
        self.view_id = view_id
        self.criterios = []

    def _elementos(self):
        almacen = getattr(self.doc, "elementos", None)
        elementos = list(almacen.values()) if isinstance(almacen, dict) else []
        if self.view_id is not None:
            elementos = [e for e in elementos if self.view_id.Value in getattr(e, "en_vistas", ())]
        for criterio in self.criterios:
            elementos = [e for e in elementos if criterio(e)]
        return elementos

    def OfClass(self, clase):
        self.criterios.append(lambda e: isinstance(e, clase))
        return self

    def OfCategory(self, categoria):
        self.criterios.append(lambda e: getattr(e, "bic", None) is categoria)
        return self

    def OfCategoryId(self, categoria_id):
        self.criterios.append(
            lambda e: getattr(e, "Category", None) is not None and e.Category.Id == categoria_id
        )
        return self

    def WhereElementIsNotElementType(self):
        self.criterios.append(lambda e: not isinstance(e, ElementType))
        return self

    def WhereElementIsElementType(self):
        self.criterios.append(lambda e: isinstance(e, ElementType))
        return self

    def WherePasses(self, filtro):
        if hasattr(filtro, "pasa"):
            self.criterios.append(filtro.pasa)
        return self

    def ToElements(self):
        return self._elementos()

    def ToElementIds(self):
        return [e.Id for e in self._elementos()]

    def GetElementCount(self):
        return len(self._elementos())

    def FirstElement(self):
        elementos = self._elementos()
        return elementos[0] if elementos else None

    def __iter__(self):
        return iter(self._elementos())


class WorksetId(object):
    def __init__(self, valor):
        self.IntegerValue = int(valor)
        self.Value = int(valor)

    def __eq__(self, otro):
        return int(getattr(otro, "IntegerValue", otro)) == self.IntegerValue

    def __ne__(self, otro):
        return not self.__eq__(otro)

    def __hash__(self):
        return hash(self.IntegerValue)


class FilteredWorksetCollector(object):
    def __init__(self, doc):
        self.doc = doc

    def OfKind(self, kind):
        return list(getattr(self.doc, "worksets", []))


class DesignOption(object):
    @staticmethod
    def GetActiveDesignOptionId(doc):
        return ElementId(-1)
