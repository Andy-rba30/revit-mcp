# -*- coding: utf-8 -*-
"""Modelo de Revit simulado para las pruebas de la fase 2 (CPython, sin Revit).

- `Doc`: documento con `elementos` (dict id -> elemento), vista activa, fases,
  subproyectos, avisos, `agregar()` para las fabricas de DB (Grid.Create...),
  y `Link` / `AcquireCoordinates` / `ActiveProjectLocation` simulados.
- `Elemento`: base con parametros (`Parametro`), categoria, tipo, nivel, caja
  envolvente, anfitrion, insertos, uniones, dependientes, vistas en las que es
  visible y solidos. Se combina con la clase de DB que toque:
  `class Muro(Elemento, DB.Wall)`.
- `SpecTypeId`: tipos de dato de los parametros Double; las pruebas lo activan
  con `activar_spec(monkeypatch)` porque tests/test_correcciones_021.py lo
  define y lo borra del modulo DB.

Todo esta en un Revit "en espanol": los nombres visibles de parametros y
categorias llevan tildes, y los alias ingleses se resuelven por BuiltInParameter.
"""
from pyrevit import DB

MM_TO_FEET = 1.0 / 304.8


class SpecTypeId(object):
    Length = DB._Enum("Length")
    Area = DB._Enum("Area")
    Volume = DB._Enum("Volume")
    Angle = DB._Enum("Angle")
    Number = DB._Enum("Number")

    # 0.6.0 (editor de familias): los grupos anidados de SpecTypeId
    class Int(object):
        Integer = DB._Enum("Integer")

    class String(object):
        Text = DB._Enum("Text")
        Url = DB._Enum("Url")

    class Boolean(object):
        YesNo = DB._Enum("YesNo")

    class Reference(object):
        Material = DB._Enum("Material")


def activar_spec(monkeypatch):
    """Deja DB.SpecTypeId apuntando al de este modulo durante la prueba."""
    monkeypatch.setattr(DB, "SpecTypeId", SpecTypeId, raising=False)


class Definicion(object):
    def __init__(self, nombre, bip=None, spec=None):
        self.Name = nombre
        self.BuiltInParameter = bip if bip is not None else DB.BuiltInParameter.INVALID
        self._spec = spec

    def GetDataType(self):
        if self._spec is None:
            raise Exception("no data type")
        return self._spec

    def GetGroupTypeId(self):
        return "grupo"


class Parametro(object):
    """Parametro simulado. Los Double se guardan en unidades internas (pies)."""

    def __init__(self, nombre, valor=None, tipo="String", bip=None, spec=None, solo_lectura=False,
                 compartido=False, guid=None, visible=None, elem_id=None):
        self.Definition = Definicion(nombre, bip, spec)
        self.StorageType = getattr(DB.StorageType, tipo)
        self._valor = valor
        self.IsReadOnly = solo_lectura
        self.IsShared = compartido
        self._guid = guid
        self._visible = visible
        self.bip = bip
        self.Id = DB.ElementId(elem_id) if elem_id is not None else DB.ElementId(-1)

    @property
    def HasValue(self):
        return self._valor is not None

    @property
    def GUID(self):
        if not self.IsShared:
            raise Exception("Parameter is not shared")
        return self._guid

    def AsString(self):
        return self._valor if self.StorageType is DB.StorageType.String else None

    def AsInteger(self):
        return int(self._valor) if self._valor is not None else 0

    def AsDouble(self):
        return float(self._valor) if self._valor is not None else 0.0

    def AsElementId(self):
        if self.StorageType is DB.StorageType.ElementId:
            return self._valor if isinstance(self._valor, DB.ElementId) else DB.ElementId(self._valor if self._valor is not None else -1)
        return DB.ElementId.InvalidElementId

    def AsValueString(self):
        return self._visible

    def Set(self, valor):
        if self.IsReadOnly:
            return False
        self._valor = valor
        # 0.5.0: como en Revit, el texto visible de una longitud refleja el valor nuevo
        if self.StorageType is DB.StorageType.Double and self.Definition._spec is SpecTypeId.Length and valor is not None:
            mm = float(valor) / MM_TO_FEET
            self._visible = u"{} mm".format(int(round(mm)) if abs(mm - round(mm)) < 1e-6 else round(mm, 2))
        return True


def texto(nombre, valor, bip=None, **extra):
    return Parametro(nombre, valor, "String", bip=bip, **extra)


def entero(nombre, valor, bip=None, **extra):
    return Parametro(nombre, valor, "Integer", bip=bip, **extra)


def longitud_mm(nombre, mm, bip=None, **extra):
    """Parametro Double de longitud: `mm` se guarda en pies, se muestra en mm."""
    valor = None if mm is None else float(mm) * MM_TO_FEET
    visible = None if mm is None else u"{} mm".format(int(mm) if float(mm).is_integer() else mm)
    return Parametro(nombre, valor, "Double", bip=bip, spec=SpecTypeId.Length, visible=visible, **extra)


def referencia(nombre, elem_id, bip=None, **extra):
    return Parametro(nombre, DB.ElementId(elem_id) if elem_id is not None else None, "ElementId", bip=bip, **extra)


def caja_mm(xmin, ymin, zmin, xmax, ymax, zmax):
    return DB.BoundingBoxXYZ(
        DB.XYZ(xmin * MM_TO_FEET, ymin * MM_TO_FEET, zmin * MM_TO_FEET),
        DB.XYZ(xmax * MM_TO_FEET, ymax * MM_TO_FEET, zmax * MM_TO_FEET),
    )


class Ubicacion(object):
    """Location con Point o Curve segun se construya."""

    def __init__(self, punto=None, curva=None):
        if punto is not None:
            self.Point = punto
            self.Rotation = 0.0
        if curva is not None:
            self.Curve = curva


class Elemento(DB.Element):
    def __init__(self, doc, identificador, nombre=None, categoria=u"Muros", bic=None, tipo_id=None,
                 nivel_id=None, caja=None, parametros=(), unique_id=None, categoria_tipo=None):
        DB.Element.__init__(self)
        self.doc = doc
        self.Id = DB.ElementId(identificador)
        self.Name = nombre
        self.bic = bic
        self.Category = DB.Category(categoria, bic, categoria_tipo) if categoria else None
        self.type_id = DB.ElementId(tipo_id) if tipo_id is not None else DB.ElementId.InvalidElementId
        self.LevelId = DB.ElementId(nivel_id) if nivel_id is not None else DB.ElementId.InvalidElementId
        self.caja = caja
        self.Parameters = list(parametros)
        self.UniqueId = unique_id or u"uid-{}".format(identificador)
        self.insertos = []
        self.unidos = []
        self.dependientes = []
        self.en_vistas = set()
        self.solidos = []
        self.CreatedPhaseId = DB.ElementId.InvalidElementId
        self.DemolishedPhaseId = DB.ElementId.InvalidElementId
        self.DesignOption = None
        self.WorksetId = None
        if doc is not None:
            doc.elementos[identificador] = self

    def get_Geometry(self, opciones):
        return list(self.solidos)

    def mover(self, delta):
        if self.caja is not None:
            self.caja = DB.BoundingBoxXYZ(self.caja.Min.Add(delta), self.caja.Max.Add(delta))
        if self.Location is not None and hasattr(self.Location, "Point"):
            self.Location = Ubicacion(punto=self.Location.Point.Add(delta))

    def parametro(self, nombre):
        return self.LookupParameter(nombre)


class Muro(Elemento, DB.Wall):
    pass


class Suelo(Elemento, DB.Floor):
    pass


class Puerta(Elemento, DB.FamilyInstance):
    def __init__(self, doc, identificador, host=None, **kw):
        Elemento.__init__(self, doc, identificador, **kw)
        self.Host = host
        self.SuperComponent = None


class TipoFamilia(Elemento, DB.FamilySymbol):
    def __init__(self, doc, identificador, familia=None, **kw):
        Elemento.__init__(self, doc, identificador, **kw)
        self.Family = familia
        self.IsActive = True


class TipoMuro(Elemento, DB.WallType):
    pass


class Familia(Elemento, DB.Family):
    IsInPlace = False
    IsEditable = True

    @property
    def FamilyCategory(self):
        return self.Category

    @FamilyCategory.setter
    def FamilyCategory(self, categoria):
        self.Category = categoria
        self.bic = categoria.BuiltInCategory if categoria is not None else None

    def GetFamilySymbolIds(self):
        return [e.Id for e in self.doc.elementos.values()
                if isinstance(e, DB.FamilySymbol) and getattr(e, "Family", None) is self] if self.doc else []


class Nivel(Elemento, DB.Level):
    def __init__(self, doc, identificador, nombre, elevacion_mm, **kw):
        kw.setdefault("categoria", u"Niveles")
        kw.setdefault("bic", DB.BuiltInCategory.OST_Levels)
        kw.setdefault("categoria_tipo", DB.CategoryType.Annotation)
        Elemento.__init__(self, doc, identificador, nombre=nombre, **kw)
        self.Elevation = float(elevacion_mm) * MM_TO_FEET


class Rejilla(Elemento, DB.Grid):
    def __init__(self, doc, identificador, nombre, **kw):
        kw.setdefault("categoria", u"Rejillas")
        kw.setdefault("bic", DB.BuiltInCategory.OST_Grids)
        kw.setdefault("categoria_tipo", DB.CategoryType.Annotation)
        Elemento.__init__(self, doc, identificador, nombre=nombre, **kw)


class Fase(Elemento, DB.Phase):
    pass


class VistaPlanta(Elemento, DB.ViewPlan):
    def __init__(self, doc, identificador, nombre, nivel=None, **kw):
        kw.setdefault("categoria", u"Vistas")
        kw.setdefault("bic", DB.BuiltInCategory.OST_Views)
        kw.setdefault("categoria_tipo", DB.CategoryType.Annotation)
        Elemento.__init__(self, doc, identificador, nombre=nombre, **kw)
        self.GenLevel = nivel
        self.IsTemplate = False
        self.ViewType = DB.ViewType.FloorPlan
        self.Scale = 100
        self.CropBoxActive = False
        self.CropBoxVisible = False
        self.CropBox = None
        self.Discipline = DB.ViewDiscipline.Architectural
        self.DetailLevel = DB.ViewDetailLevel.Medium
        self.ViewTemplateId = DB.ElementId.InvalidElementId
        self.rango = DB.PlanViewRange()
        self.SketchPlane = None


class Vista3D(Elemento, DB.View3D):
    def __init__(self, doc, identificador, nombre, **kw):
        kw.setdefault("categoria", u"Vistas")
        kw.setdefault("bic", DB.BuiltInCategory.OST_Views)
        kw.setdefault("categoria_tipo", DB.CategoryType.Annotation)
        Elemento.__init__(self, doc, identificador, nombre=nombre, **kw)
        self.IsTemplate = False
        self.ViewType = DB.ViewType.ThreeD
        self.IsSectionBoxActive = False
        self.caja_seccion = None


class Plano(Elemento, DB.ViewSheet):
    def __init__(self, doc, identificador, numero, nombre, **kw):
        kw.setdefault("categoria", u"Planos")
        kw.setdefault("bic", DB.BuiltInCategory.OST_Sheets)
        kw.setdefault("categoria_tipo", DB.CategoryType.Annotation)
        Elemento.__init__(self, doc, identificador, nombre=nombre, **kw)
        self.SheetNumber = numero
        self.IsTemplate = False
        self.ViewType = DB.ViewType.DrawingSheet


class Tabla(Elemento, DB.ViewSchedule):
    def __init__(self, doc, identificador, nombre, campos, filas, mostrar_encabezados=True, titulo=None, **kw):
        kw.setdefault("categoria", u"Tablas de planificación")
        kw.setdefault("bic", DB.BuiltInCategory.OST_Views)
        kw.setdefault("categoria_tipo", DB.CategoryType.Annotation)
        Elemento.__init__(self, doc, identificador, nombre=nombre, **kw)
        self.IsTemplate = False
        self.ViewType = DB.ViewType.Schedule
        self.Definition = DB.ScheduleDefinition(
            [c if isinstance(c, DB.ScheduleField) else DB.ScheduleField(c) for c in campos], mostrar_encabezados
        )
        self.filas = list(filas)
        self.titulo = [[titulo or nombre]]


class Cota(Elemento, DB.Dimension):
    def __init__(self, doc, identificador, referenciados, vista_id, **kw):
        kw.setdefault("categoria", u"Cotas")
        kw.setdefault("bic", DB.BuiltInCategory.OST_Dimensions)
        kw.setdefault("categoria_tipo", DB.CategoryType.Annotation)
        Elemento.__init__(self, doc, identificador, **kw)
        self.References = [DB.Reference(DB.ElementId(i)) for i in referenciados]
        self.en_vistas = set([vista_id])
        self.ViewSpecific = True
        self.OwnerViewId = DB.ElementId(vista_id)


class Etiqueta(Elemento, DB.IndependentTag):
    def __init__(self, doc, identificador, etiquetados, vista_id, **kw):
        kw.setdefault("categoria", u"Etiquetas de muro")
        kw.setdefault("bic", DB.BuiltInCategory.OST_WallTags)
        kw.setdefault("categoria_tipo", DB.CategoryType.Annotation)
        Elemento.__init__(self, doc, identificador, **kw)
        self.etiquetados = list(etiquetados)
        self.en_vistas = set([vista_id])
        self.ViewSpecific = True
        self.OwnerViewId = DB.ElementId(vista_id)


class Vinculo(Elemento, DB.ImportInstance):
    pass


class Aviso(object):
    def __init__(self, descripcion, ids, definicion=None, severidad=None, adicionales=()):
        self.descripcion = descripcion
        self.ids = list(ids)
        self.definicion = definicion
        self.severidad = severidad or DB.FailureSeverity.Warning
        self.adicionales = list(adicionales)

    def GetSeverity(self):
        return self.severidad

    def GetDescriptionText(self):
        return self.descripcion

    def GetFailingElements(self):
        return [DB.ElementId(i) for i in self.ids]

    def GetAdditionalElements(self):
        return [DB.ElementId(i) for i in self.adicionales]

    def GetFailureDefinitionId(self):
        if self.definicion is None:
            raise Exception("sin definicion")
        return self.definicion


class _IdWorkset(object):
    def __init__(self, valor):
        self.IntegerValue = int(valor)
        self.Value = int(valor)


class Workset(object):
    def __init__(self, identificador, nombre, propietario=None, editable=True):
        self.Id = _IdWorkset(identificador)
        self.Name = nombre
        self.Owner = propietario
        self.IsEditable = editable
        self.IsOpen = True
        self.IsDefaultWorkset = False
        self.IsVisibleByDefault = True


class _TablaWorksets(object):
    def __init__(self, doc):
        self.doc = doc

    def GetActiveWorksetId(self):
        return _IdWorkset(self.doc.worksets[0].Id.IntegerValue if self.doc.worksets else 0)

    def GetWorkset(self, ws_id):
        for ws in self.doc.worksets:
            if ws.Id.IntegerValue == int(getattr(ws_id, "IntegerValue", ws_id)):
                return ws
        return None


class PosicionProyecto(object):
    def __init__(self, este=0.0, norte=0.0, elevacion=0.0, angulo=0.0):
        self.EastWest = este
        self.NorthSouth = norte
        self.Elevation = elevacion
        self.Angle = angulo


class UbicacionProyecto(object):
    def __init__(self, doc):
        self.doc = doc
        self.Id = DB.ElementId(-9)
        self.Name = u"Interno"

    def GetProjectPosition(self, punto):
        return self.doc.posicion

    def SetProjectPosition(self, punto, posicion):
        self.doc.posicion = posicion

    def GetSiteLocation(self):
        return self


class _Fases(list):
    @property
    def Size(self):
        return len(self)

    def get_Item(self, indice):
        return self[indice]


class Instancia(Elemento, DB.FamilyInstance):
    """FamilyInstance creada por doc.Create.NewFamilyInstance."""

    def __init__(self, doc, symbol, **kw):
        kw.setdefault("categoria", symbol.Category.Name if symbol.Category else u"Modelos genéricos")
        kw.setdefault("bic", symbol.Category.BuiltInCategory if symbol.Category else None)
        Elemento.__init__(self, None, 0, nombre=None, tipo_id=symbol.Id.Value, **kw)
        self.symbol = symbol
        self.Host = None
        self.SuperComponent = None
        self.Parameters = [
            texto(u"Comentarios", u"", bip=DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
            texto(u"Marca", u"", bip=DB.BuiltInParameter.ALL_MODEL_MARK),
            referencia(u"Nivel superior", None, bip=DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM),
            longitud_mm(u"Desfase superior", 0, bip=DB.BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM),
        ]
        doc.agregar(self)


class Hueco(Elemento):
    pass


class Habitacion(Elemento):
    def __init__(self, doc, nivel, **kw):
        kw.setdefault("categoria", u"Habitaciones")
        kw.setdefault("bic", DB.BuiltInCategory.OST_Rooms)
        Elemento.__init__(self, None, 0, nivel_id=nivel.Id.Value if nivel is not None else None, **kw)
        self.Parameters = [
            texto(u"Nombre", u"Habitación", bip=DB.BuiltInParameter.ROOM_NAME),
            texto(u"Número", u"1", bip=DB.BuiltInParameter.ROOM_NUMBER),
            Parametro(u"Área", 200.0, "Double", bip=DB.BuiltInParameter.HOST_AREA_COMPUTED, solo_lectura=True),
        ]
        doc.agregar(self)


class LineaDetalle(Elemento):
    LineStyle = None


class _Fabricas(object):
    """doc.Create: las fabricas de Autodesk.Revit.Creation.Document que usan los helpers."""

    def __init__(self, doc):
        self.doc = doc
        self.llamadas = []

    def NewFamilyInstance(self, *args):
        self.llamadas.append(("NewFamilyInstance", args))
        if isinstance(args[0], DB.Reference):
            # 0.5.0: familia alojada en cara: (Reference, XYZ, XYZ refDir, FamilySymbol)
            referencia, punto, direccion, symbol = args[0], args[1], args[2], args[3]
            instancia = Instancia(self.doc, symbol)
            instancia.Location = Ubicacion(punto=punto)
            instancia.referencia_cara = referencia
            instancia.direccion = direccion
            instancia.Host = self.doc.GetElement(referencia.ElementId)
            return instancia
        geometria, symbol = args[0], args[1]
        resto = list(args[2:])
        instancia = Instancia(self.doc, symbol)
        if isinstance(geometria, DB.Line):
            instancia.Location = Ubicacion(curva=geometria)
        else:
            instancia.Location = Ubicacion(punto=geometria)
        for extra in resto:
            if isinstance(extra, DB.Level):
                instancia.LevelId = extra.Id
            elif isinstance(extra, DB.Wall):
                instancia.Host = extra
            elif isinstance(extra, DB._Enum):
                instancia.structural_type = extra
        return instancia

    def NewFootPrintRoof(self, curvas, nivel, tipo, ref_curvas):
        self.llamadas.append(("NewFootPrintRoof", (curvas, nivel, tipo)))
        cubierta = DB.FootPrintRoof()
        cubierta.curvas = list(curvas)
        return DB._registrar_creado(self.doc, cubierta, u"Cubiertas", DB.BuiltInCategory.OST_Roofs, tipo.Id, nivel.Id)

    def NewOpening(self, host, *args):
        self.llamadas.append(("NewOpening", (host,) + args))
        hueco = Hueco(None, 0, categoria=u"Huecos", bic=None)
        hueco.host_id = host.Id
        hueco.argumentos = args
        self.doc.agregar(hueco)
        return hueco

    def NewRoom(self, *args):
        self.llamadas.append(("NewRoom", args))
        nivel = args[0] if isinstance(args[0], DB.Level) else None
        return Habitacion(self.doc, nivel)

    def NewRoomBoundaryLines(self, plano, curvas, vista):
        self.llamadas.append(("NewRoomBoundaryLines", (plano, curvas, vista)))
        lineas = []
        for curva in curvas:
            linea = Elemento(None, 0, categoria=u"Separación de habitación", bic=DB.BuiltInCategory.OST_Lines)
            linea.Curve = curva
            self.doc.agregar(linea)
            lineas.append(linea)
        return lineas

    def NewDetailCurve(self, vista, curva):
        self.llamadas.append(("NewDetailCurve", (vista, curva)))
        linea = LineaDetalle(None, 0, categoria=u"Líneas", bic=DB.BuiltInCategory.OST_Lines)
        linea.Curve = curva
        linea.vista_id = vista.Id
        self.doc.agregar(linea)
        return linea


class _FabricaAplicacion(object):
    """app.Create: NewCurveLoopsProfile (barridos)."""

    def NewCurveLoopsProfile(self, lazos):
        return DB.SweepProfile(lazos)


class Aplicacion(object):
    """doc.Application: rutas de biblioteca (0.5.0) y, en 0.6.0, documentos abiertos,
    plantillas de familia (FamilyTemplatePath), NewFamilyDocument, OpenDocumentFile y
    el archivo de parametros compartidos."""

    def __init__(self):
        self.bibliotecas = {}
        self.Language = "Spanish"
        self.VersionNumber = "2027"
        self.Documents = []
        self.FamilyTemplatePath = ""
        self.Create = _FabricaAplicacion()
        self.archivo_compartidos = None
        self.plantillas_invalidas = set()

    def GetLibraryPaths(self):
        return dict(self.bibliotecas)

    def NewFamilyDocument(self, ruta_plantilla):
        import os

        if not os.path.isfile(ruta_plantilla) or ruta_plantilla in self.plantillas_invalidas:
            raise Exception("Revit: the family template could not be opened: {}".format(ruta_plantilla))
        numero = 1 + len([d for d in self.Documents if getattr(d, "IsFamilyDocument", False)])
        doc = DocFamilia(self, titulo=u"Familia{}".format(numero), plantilla=ruta_plantilla)
        return doc

    def OpenDocumentFile(self, ruta):
        import os

        if not os.path.isfile(ruta):
            raise Exception("Revit: file not found: {}".format(ruta))
        titulo = os.path.splitext(os.path.basename(ruta))[0]
        return DocFamilia(self, titulo=titulo, ruta=ruta)

    def OpenSharedParameterFile(self):
        return self.archivo_compartidos


class Material(Elemento, DB.Material):
    def __init__(self, doc, identificador, nombre, activo_id=None, **kw):
        kw.setdefault("categoria", u"Materiales")
        kw.setdefault("bic", DB.BuiltInCategory.OST_Materials)
        Elemento.__init__(self, doc, identificador, nombre=nombre, **kw)
        self.StructuralAssetId = DB.ElementId(activo_id) if activo_id is not None else DB.ElementId.InvalidElementId


class ActivoEstructural(Elemento, DB.PropertySetElement):
    """PropertySetElement con un StructuralAsset (densidad en kg/m3 y clase)."""

    def __init__(self, doc, identificador, nombre, densidad_kg_m3, clase=None, **kw):
        kw.setdefault("categoria", None)
        Elemento.__init__(self, doc, identificador, nombre=nombre, **kw)
        self.activo = DB.StructuralAsset(densidad_kg_m3, clase if clase is not None else DB.StructuralAssetClass.Metal)


class MiembroAnalitico(Elemento, DB.Structure.AnalyticalMember):
    """AnalyticalMember asociado a un elemento fisico (doc.asociar los enlaza)."""

    def __init__(self, doc, identificador, inicio_mm, fin_mm, **kw):
        kw.setdefault("categoria", u"Miembros analíticos")
        kw.setdefault("categoria_tipo", DB.CategoryType.AnalyticalModel)
        DB.Structure.AnalyticalMember.__init__(self)
        Elemento.__init__(self, doc, identificador, **kw)
        self.curva = DB.Line.CreateBound(
            DB.XYZ(inicio_mm[0] * MM_TO_FEET, inicio_mm[1] * MM_TO_FEET, inicio_mm[2] * MM_TO_FEET),
            DB.XYZ(fin_mm[0] * MM_TO_FEET, fin_mm[1] * MM_TO_FEET, fin_mm[2] * MM_TO_FEET),
        )


class TipoCercha(Elemento, DB.Structure.TrussType):
    def __init__(self, doc, identificador, nombre, **kw):
        kw.setdefault("categoria", u"Cerchas estructurales")
        kw.setdefault("bic", DB.BuiltInCategory.OST_StructuralTruss)
        Elemento.__init__(self, doc, identificador, nombre=nombre, **kw)
        self.FamilyName = u"Cercha"


def instalar_conexiones(monkeypatch):
    """Anade a DB.Structure las clases del modulo de conexiones de acero (simulado).

    Sin llamarla, DB.Structure no tiene StructuralConnectionHandler y las rutas de
    conexiones responden 409 no_soportado, como en un Revit sin el modulo."""

    class StructuralConnectionHandlerType(DB.ElementType):
        @staticmethod
        def GetDefaultConnectionHandlerType(doc):
            # como la API real: devuelve el ElementId del tipo, no el tipo (0.5.1)
            for elemento in doc.elementos.values():
                if isinstance(elemento, StructuralConnectionHandlerType):
                    return elemento.Id
            return DB.ElementId.InvalidElementId

    class StructuralConnectionApprovalType(DB.ElementType):
        @staticmethod
        def GetAllStructuralConnectionApprovalTypes(doc):
            return [e.Id for e in doc.elementos.values() if isinstance(e, StructuralConnectionApprovalType)]

    class StructuralConnectionHandler(DB.Element):
        ApprovalStatus = DB.ElementId.InvalidElementId

        @staticmethod
        def Create(doc, ids, tipo_id):
            if len(ids) < 1:
                raise Exception("Revit: a connection needs at least one element")
            conexion = StructuralConnectionHandler()
            conexion.conectados = [i for i in ids]
            return DB._registrar_creado(doc, conexion, u"Conexiones estructurales",
                                        DB.BuiltInCategory.OST_StructConnections, tipo_id)

        def GetConnectedElementIds(self):
            return list(self.conectados)

    monkeypatch.setattr(DB.Structure, "StructuralConnectionHandlerType", StructuralConnectionHandlerType, raising=False)
    monkeypatch.setattr(DB.Structure, "StructuralConnectionApprovalType", StructuralConnectionApprovalType, raising=False)
    monkeypatch.setattr(DB.Structure, "StructuralConnectionHandler", StructuralConnectionHandler, raising=False)
    return StructuralConnectionHandlerType, StructuralConnectionApprovalType, StructuralConnectionHandler


class Doc(object):
    """Documento simulado. `ruta` vacia = sin guardar (log y snapshots en %LOCALAPPDATA%)."""

    def __init__(self, ruta="", titulo=u"Modelo"):
        self.Create = _Fabricas(self)
        self.Application = Aplicacion()
        self.PathName = ruta
        self.Title = titulo
        self.IsWorkshared = False
        self.IsReadOnly = False
        self.IsModifiable = False
        self.elementos = {}
        self.transacciones = []
        self.avisos = []
        self.worksets = []
        self.Phases = _Fases()
        self.ActiveView = None
        self.posicion = PosicionProyecto()
        self.ActiveProjectLocation = UbicacionProyecto(self)
        self.ProjectLocations = [self.ActiveProjectLocation]
        self.vinculados = []
        self.coordenadas_adquiridas = []
        self.regeneraciones = 0
        # 0.5.0: asociaciones fisico <-> analitico, familias que LoadFamily puede cargar y exportaciones
        self.asociaciones = {}
        self.familias_cargables = {}
        self.cargas = []
        self.exportaciones = []
        self._siguiente_id = 900000

    # --- 0.5.0: modelo analitico, familias y exportacion -----------------
    def asociar(self, fisico, analitico):
        """Enlaza un elemento fisico con su miembro analitico (en los dos sentidos)."""
        self.asociaciones[fisico.Id.Value] = analitico.Id.Value
        self.asociaciones[analitico.Id.Value] = fisico.Id.Value

    def _familia_por_nombre(self, nombre):
        for elemento in self.elementos.values():
            if isinstance(elemento, DB.Family) and elemento.Name == nombre:
                return elemento
        return None

    def _cargar(self, ruta, tipos):
        """Crea (o completa) la familia descrita en familias_cargables[ruta]. Devuelve (nueva, familia, simbolos)."""
        if not self.IsModifiable:
            raise Exception("Revit: Attempt to modify the model outside of transaction")
        descripcion = self.familias_cargables.get(ruta)
        if descripcion is None:
            raise Exception("Revit: family file could not be loaded: {}".format(ruta))
        nombre, categoria, bic = descripcion["nombre"], descripcion["categoria"], descripcion["bic"]
        familia = self._familia_por_nombre(nombre)
        nueva = familia is None
        if nueva:
            familia = Familia(None, 0, nombre=nombre, categoria=categoria, bic=bic)
            familia.StructuralMaterialType = descripcion.get("material", DB.Structure.StructuralMaterialType.Steel)
            self.agregar(familia)
            familia.doc = self
        simbolos = []
        for tipo in tipos:
            existente = [e for e in self.elementos.values()
                         if isinstance(e, DB.FamilySymbol) and getattr(e, "Family", None) is familia and e.Name == tipo]
            if existente:
                simbolos.append((False, existente[0]))
                continue
            simbolo = TipoFamilia(None, 0, familia=familia, nombre=tipo, categoria=categoria, bic=bic)
            simbolo.IsActive = False
            self.agregar(simbolo)
            simbolos.append((True, simbolo))
        self.cargas.append((ruta, list(tipos)))
        return nueva, familia, simbolos

    def LoadFamily(self, ruta, referencia=None):
        """LoadFamily(ruta, out Family): False si la familia ya estaba cargada (como en Revit)."""
        descripcion = self.familias_cargables.get(ruta) or {}
        nueva, familia, _ = self._cargar(ruta, descripcion.get("tipos", []))
        if referencia is not None:
            referencia.Value = familia
            return nueva
        return nueva, familia

    def LoadFamilySymbol(self, ruta, nombre_tipo, referencia=None):
        """LoadFamilySymbol(ruta, tipo, out symbol): carga un tipo del catalogo."""
        descripcion = self.familias_cargables.get(ruta) or {}
        if nombre_tipo not in descripcion.get("catalogo", descripcion.get("tipos", [])):
            raise Exception("Revit: type '{}' not found in the type catalog".format(nombre_tipo))
        _, _, simbolos = self._cargar(ruta, [nombre_tipo])
        nuevo, simbolo = simbolos[0]
        if referencia is not None:
            referencia.Value = simbolo
        return nuevo

    def Export(self, carpeta, nombre, opciones):
        import os

        ruta = os.path.join(carpeta, nombre if nombre.lower().endswith(".ifc") else nombre + ".ifc")
        with open(ruta, "wb") as archivo:
            archivo.write(b"ISO-10303-21;\n")
        self.exportaciones.append((ruta, opciones))
        return True

    # --- elementos -----------------------------------------------------
    def GetElement(self, elem_id):
        if isinstance(elem_id, DB.ElementId):
            return self.elementos.get(elem_id.Value)
        return None

    def agregar(self, elemento, identificador=None):
        if identificador is None:
            identificador = self._siguiente_id
            self._siguiente_id += 1
        elemento.Id = DB.ElementId(identificador)
        if getattr(elemento, "UniqueId", None) is None:
            elemento.UniqueId = u"uid-{}".format(identificador)
        for atributo, valor in (("insertos", []), ("unidos", []), ("dependientes", []), ("en_vistas", set()), ("solidos", [])):
            if not hasattr(elemento, atributo):
                setattr(elemento, atributo, valor)
        self.elementos[identificador] = elemento
        return elemento

    def Delete(self, elem_id):
        self.elementos.pop(elem_id.Value, None)
        return [elem_id]

    def Regenerate(self):
        self.regeneraciones += 1

    def GetWarnings(self):
        return list(self.avisos)

    def GetWorksetTable(self):
        return _TablaWorksets(self)

    # --- vinculos y coordenadas ---------------------------------------
    def Link(self, ruta, opciones, vista, idref):
        vinculo = Vinculo(None, 0, nombre=ruta.split("\\")[-1].split("/")[-1], categoria=u"Vínculos CAD",
                          bic=DB.BuiltInCategory.OST_Lines)
        vinculo.ruta = ruta
        vinculo.placement = getattr(opciones, "Placement", None)
        vinculo.vista_id = vista.Id if vista is not None else None
        vinculo.Location = Ubicacion(punto=DB.XYZ(0, 0, 0))
        self.agregar(vinculo)
        self.vinculados.append(vinculo)
        idref.Value = vinculo.Id
        return True

    def Import(self, ruta, opciones, vista, idref):
        return self.Link(ruta, opciones, vista, idref)

    def AcquireCoordinates(self, elem_id):
        if self.GetElement(elem_id) is None:
            raise Exception("element not found")
        self.coordenadas_adquiridas.append(elem_id.Value)
        self.posicion = PosicionProyecto(1000.0, 2000.0, 10.0, 0.1)


# ---------------------------------------------------------------------------
# 0.6.0 (entrega 2c): documento de familia simulado
# ---------------------------------------------------------------------------
class PlanoReferencia(DB.ReferencePlane):
    """ReferencePlane con Name, plano (Origin, Normal), referencia y ELEM_REFERENCE_NAME."""

    def __init__(self, doc, bubble, free, cut, vista):
        DB.ReferencePlane.__init__(self)
        self.BubbleEnd = bubble
        self.FreeEnd = free
        direccion = free.Subtract(bubble).Normalize()
        self.Normal = direccion.CrossProduct(cut).Normalize()
        self.plano = DB.Plane(self.Normal, bubble, direccion, cut.Normalize())
        self.vista_id = vista.Id if vista is not None else None
        self.Parameters = [entero(u"Es referencia", 1, bip=DB.BuiltInParameter.ELEM_REFERENCE_NAME)]
        self.Name = None
        DB._registrar_creado(doc, self, u"Planos de referencia", DB.BuiltInCategory.OST_CLines,
                             tipo_categoria=DB.CategoryType.Annotation)


class CotaFamilia(DB.Dimension):
    """Dimension de familia: References, FamilyLabel, AreSegmentsEqual."""

    def __init__(self, doc, vista, linea, referencias):
        DB.Dimension.__init__(self)
        self.References = list(referencias)
        self.linea = linea
        self.FamilyLabel = None
        self.AreSegmentsEqual = False
        self.OwnerViewId = vista.Id
        self.ViewSpecific = True
        DB._registrar_creado(doc, self, u"Cotas", DB.BuiltInCategory.OST_Dimensions,
                             tipo_categoria=DB.CategoryType.Annotation)

    @property
    def NumberOfSegments(self):
        return max(len(self.References) - 1, 0)


def _area_lazo(lazo):
    """Area (pies2) del poligono/circulo de un CurveArray simulado."""
    puntos = []
    for curva in lazo:
        if isinstance(curva, DB.Arc):
            import math

            return math.pi * curva.Radius ** 2 if len(list(lazo)) <= 2 else 0.0
        puntos.append(curva.GetEndPoint(0))
    if len(puntos) < 3:
        return 0.0
    area = 0.0
    for i in range(len(puntos)):
        a, b = puntos[i], puntos[(i + 1) % len(puntos)]
        area += a.X * b.Y - b.X * a.Y
    return abs(area) / 2.0


def _caras_caja(solido, xmin, ymin, zmin, xmax, ymax, zmax):
    """Seis caras planas con referencia para un prisma (bloqueos y conectores)."""
    caras = []
    for nombre, normal, origen in (
        ("top", DB.XYZ(0, 0, 1), DB.XYZ((xmin + xmax) / 2, (ymin + ymax) / 2, zmax)),
        ("bottom", DB.XYZ(0, 0, -1), DB.XYZ((xmin + xmax) / 2, (ymin + ymax) / 2, zmin)),
        ("right", DB.XYZ(1, 0, 0), DB.XYZ(xmax, (ymin + ymax) / 2, (zmin + zmax) / 2)),
        ("left", DB.XYZ(-1, 0, 0), DB.XYZ(xmin, (ymin + ymax) / 2, (zmin + zmax) / 2)),
        ("back", DB.XYZ(0, 1, 0), DB.XYZ((xmin + xmax) / 2, ymax, (zmin + zmax) / 2)),
        ("front", DB.XYZ(0, -1, 0), DB.XYZ((xmin + xmax) / 2, ymin, (zmin + zmax) / 2)),
    ):
        cara = DB.PlanarFace(normal, origen, area=1.0)
        cara.Reference = DB.Reference(solido)
        cara.Reference.cara = nombre
        caras.append(cara)
    return caras


class _FabricasFamilia(object):
    """doc.FamilyCreate: NewReferencePlane, NewDimension, NewExtrusion, NewSweep, NewRevolution, NewBlend, NewAlignment."""

    def __init__(self, doc):
        self.doc = doc
        self.llamadas = []

    def _exigir_transaccion(self):
        if not self.doc.IsModifiable:
            raise Exception("Revit: Attempt to modify the model outside of transaction")

    def NewReferencePlane(self, bubble, free, cut, vista):
        self._exigir_transaccion()
        self.llamadas.append(("NewReferencePlane", (bubble, free, cut, vista)))
        return PlanoReferencia(self.doc, bubble, free, cut, vista)

    def NewDimension(self, vista, linea, referencias):
        self._exigir_transaccion()
        refs = list(getattr(referencias, "refs", referencias))
        if len(refs) < 2:
            raise Exception("Revit: a dimension needs at least two references")
        self.llamadas.append(("NewDimension", (vista, linea, refs)))
        return CotaFamilia(self.doc, vista, linea, refs)

    def _solido(self, clase, es_solido, volumen, nombre_categoria=u"Modelos genéricos"):
        forma = clase()
        forma.IsSolid = bool(es_solido)
        forma.volumen = volumen
        forma.Parameters = [
            longitud_mm(u"Inicio de extrusión", 0, bip=DB.BuiltInParameter.EXTRUSION_START_PARAM),
            longitud_mm(u"Fin de extrusión", 0, bip=DB.BuiltInParameter.EXTRUSION_END_PARAM),
            referencia(u"Material", None, bip=DB.BuiltInParameter.MATERIAL_ID_PARAM),
        ]
        DB._registrar_creado(self.doc, forma, nombre_categoria, self.doc.OwnerFamily.bic)
        return forma

    def NewExtrusion(self, es_solido, perfil, plano, altura):
        self._exigir_transaccion()
        if float(altura) == 0:
            raise Exception("Revit: the extrusion end must differ from the start")
        area = sum(_area_lazo(lazo) for lazo in perfil)
        if area <= 0:
            raise Exception("Revit: the sketch does not form a closed loop")
        forma = self._solido(DB.Extrusion, es_solido, area * abs(float(altura)))
        forma.perfil = perfil
        forma.plano = plano
        forma.get_Parameter(DB.BuiltInParameter.EXTRUSION_END_PARAM).Set(float(altura))
        # caja aproximada a partir del primer lazo, para caras y bbox
        puntos = [c.GetEndPoint(0) for lazo in perfil for c in lazo if not isinstance(c, DB.Arc)]
        if puntos:
            xs, ys = [p.X for p in puntos], [p.Y for p in puntos]
            z0 = (plano.plano.Origin.Z if plano.plano is not None else 0.0)
            forma.caja = DB.BoundingBoxXYZ(DB.XYZ(min(xs), min(ys), z0), DB.XYZ(max(xs), max(ys), z0 + float(altura)))
            forma.caras = _caras_caja(forma, min(xs), min(ys), z0, max(xs), max(ys), z0 + float(altura))
        self.llamadas.append(("NewExtrusion", (es_solido, perfil, plano, altura)))
        return forma

    def NewSweep(self, es_solido, camino, plano, perfil, indice, ubicacion):
        self._exigir_transaccion()
        if sum(1 for lazo in camino for _ in lazo) == 0:
            raise Exception("Revit: the sweep path is empty")
        forma = self._solido(DB.Sweep, es_solido, 1.0)
        forma.camino, forma.perfil = camino, perfil
        self.llamadas.append(("NewSweep", (es_solido, camino, plano, perfil, indice, ubicacion)))
        return forma

    def NewRevolution(self, es_solido, perfil, plano, eje, inicio, fin):
        self._exigir_transaccion()
        if abs(float(fin) - float(inicio)) <= 0:
            raise Exception("Revit: the revolution angle must be positive")
        forma = self._solido(DB.Revolution, es_solido, 1.0)
        forma.perfil, forma.eje, forma.angulos = perfil, eje, (inicio, fin)
        self.llamadas.append(("NewRevolution", (es_solido, perfil, plano, eje, inicio, fin)))
        return forma

    def NewBlend(self, es_solido, superior, base, plano):
        self._exigir_transaccion()
        if _area_lazo(superior) <= 0 or _area_lazo(base) <= 0:
            raise Exception("Revit: the blend profiles must be closed loops")
        forma = self._solido(DB.Blend, es_solido, 1.0)
        forma.superior, forma.base = superior, base
        self.llamadas.append(("NewBlend", (es_solido, superior, base, plano)))
        return forma

    def NewAlignment(self, vista, ref_a, ref_b):
        self._exigir_transaccion()
        if ref_a is None or ref_b is None:
            raise Exception("Revit: NewAlignment needs two references")
        self.llamadas.append(("NewAlignment", (vista, ref_a, ref_b)))
        bloqueo = Elemento(None, 0, categoria=None)
        bloqueo.referencias = (ref_a, ref_b)
        self.doc.agregar(bloqueo)
        self.doc.alineaciones.append(bloqueo)
        return bloqueo


class VistaFamilia(Elemento, DB.View):
    def __init__(self, doc, identificador, nombre, tipo, nivel=None, direccion=None, **kw):
        kw.setdefault("categoria", u"Vistas")
        kw.setdefault("bic", DB.BuiltInCategory.OST_Views)
        kw.setdefault("categoria_tipo", DB.CategoryType.Annotation)
        Elemento.__init__(self, doc, identificador, nombre=nombre, **kw)
        self.ViewType = tipo
        self.GenLevel = nivel
        self.IsTemplate = False
        self.ViewDirection = direccion if direccion is not None else DB.XYZ(0, 0, 1)


class DocFamilia(Doc):
    """Documento de familia: IsFamilyDocument, FamilyManager, FamilyCreate, OwnerFamily, SaveAs, Close, LoadFamily."""

    def __init__(self, app, titulo=u"Familia1", plantilla=None, ruta=""):
        Doc.__init__(self, ruta, titulo)
        self.Application = app
        self.IsFamilyDocument = True
        self.IsModified = False
        self.plantilla = plantilla
        self.FamilyManager = DB.FamilyManager(self)
        self.FamilyCreate = _FabricasFamilia(self)
        self.OwnerFamily = Familia(self, 5, nombre=titulo, categoria=u"Modelos genéricos",
                                   bic=DB.BuiltInCategory.OST_GenericModel)
        self.alineaciones = []
        self.cerrado = False
        self.guardados = []
        self.al_regenerar = None
        self.cargas_en_proyecto = []
        # nivel de referencia, planta, alzado frontal y una vista 3D, como en una plantilla metrica
        nivel = Nivel(self, 10, u"Nivel de referencia", 0)
        VistaFamilia(self, 11, u"Nivel de referencia", DB.ViewType.FloorPlan, nivel=nivel)
        VistaFamilia(self, 12, u"Frontal", DB.ViewType.Elevation, direccion=DB.XYZ(0, -1, 0))
        VistaFamilia(self, 13, u"Izquierda", DB.ViewType.Elevation, direccion=DB.XYZ(-1, 0, 0))
        VistaFamilia(self, 14, u"Vista 3D", DB.ViewType.ThreeD)
        # planos de referencia centrales de la plantilla
        self.IsModifiable = True
        centro_x = self.FamilyCreate.NewReferencePlane(DB.XYZ(0, -10, 0), DB.XYZ(0, 10, 0), DB.XYZ(0, 0, 1), self.GetElement(DB.ElementId(11)))
        centro_x.Name = u"Centro (izquierda/derecha)"
        centro_y = self.FamilyCreate.NewReferencePlane(DB.XYZ(-10, 0, 0), DB.XYZ(10, 0, 0), DB.XYZ(0, 0, 1), self.GetElement(DB.ElementId(11)))
        centro_y.Name = u"Centro (delante/detrás)"
        self.IsModifiable = False
        self.FamilyManager.CurrentType = None
        tipo = DB.FamilyType(u"Familia1")
        self.FamilyManager.Types.append(tipo)
        self.FamilyManager.CurrentType = tipo
        app.Documents.append(self)

    @property
    def Title(self):
        return self._titulo

    @Title.setter
    def Title(self, valor):
        self._titulo = valor

    def Regenerate(self):
        Doc.Regenerate(self)
        if self.al_regenerar is not None:
            self.al_regenerar(self)

    def SaveAs(self, ruta, opciones=None):
        import os

        if self.IsModifiable:
            raise Exception("Revit: cannot save with an open transaction")
        if os.path.exists(ruta) and not (opciones is not None and opciones.OverwriteExistingFile):
            raise Exception("Revit: the file already exists: {}".format(ruta))
        with open(ruta, "wb") as archivo:
            archivo.write(b"RFA" + self._titulo.encode("utf-8"))
        self.PathName = ruta
        self._titulo = os.path.splitext(os.path.basename(ruta))[0]
        self.OwnerFamily.Name = self._titulo
        self.guardados.append(ruta)
        self.IsModified = False

    def Close(self, guardar=False):
        if self.IsModifiable:
            raise Exception("Revit: cannot close with an open transaction")
        if guardar and not self.PathName:
            raise Exception("Revit: the document has never been saved")
        self.cerrado = True
        if self in self.Application.Documents:
            self.Application.Documents.remove(self)
        return True

    def LoadFamily(self, proyecto, opciones=None):
        """Carga esta familia en `proyecto` (dentro de una transaccion del proyecto). Devuelve la Family."""
        if not proyecto.IsModifiable:
            raise Exception("Revit: Attempt to modify the model outside of transaction")
        nombre = self.OwnerFamily.Name or self._titulo
        existente = proyecto._familia_por_nombre(nombre)
        sobrescribir = None
        if opciones is not None:
            caja = DB.clr_StrongBox(False)
            try:
                opciones.OnFamilyFound(existente is not None, caja)
            except TypeError:
                pass
            sobrescribir = caja.Value
        if existente is not None and not sobrescribir:
            raise Exception("Revit: the family '{}' is already loaded and overwrite was refused".format(nombre))
        familia = existente
        if familia is None:
            familia = Familia(None, 0, nombre=nombre, categoria=self.OwnerFamily.Category.Name, bic=self.OwnerFamily.bic)
            proyecto.agregar(familia)
            familia.doc = proyecto
        for tipo in self.FamilyManager.Types:
            ya = [e for e in proyecto.elementos.values()
                  if isinstance(e, DB.FamilySymbol) and getattr(e, "Family", None) is familia and e.Name == tipo.Name]
            if ya:
                continue
            simbolo = TipoFamilia(None, 0, familia=familia, nombre=tipo.Name, categoria=familia.Category.Name, bic=familia.bic)
            proyecto.agregar(simbolo)
        self.cargas_en_proyecto.append((proyecto, sobrescribir))
        return familia
