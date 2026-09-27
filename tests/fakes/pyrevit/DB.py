# -*- coding: utf-8 -*-
"""Simulacion minima de Autodesk.Revit.DB para las pruebas en CPython."""


class _Enum(object):
    def __init__(self, nombre):
        self.nombre = nombre

    def __repr__(self):
        return self.nombre

    __str__ = __repr__


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
    def __init__(self, value=-1):
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


XYZ.Zero = XYZ(0, 0, 0)


class BuiltInParameter(object):
    """Atributos de ejemplo; getattr sobre nombres desconocidos falla como en Revit."""
    INVALID = _Enum("INVALID")
    FAMILY_LEVEL_PARAM = _Enum("FAMILY_LEVEL_PARAM")
    LEVEL_PARAM = _Enum("LEVEL_PARAM")
    SCHEDULE_LEVEL_PARAM = _Enum("SCHEDULE_LEVEL_PARAM")
    FAMILY_BASE_LEVEL_PARAM = _Enum("FAMILY_BASE_LEVEL_PARAM")
    ELEM_PARTITION_PARAM = _Enum("ELEM_PARTITION_PARAM")
    ALL_MODEL_INSTANCE_COMMENTS = _Enum("ALL_MODEL_INSTANCE_COMMENTS")
    ALL_MODEL_MARK = _Enum("ALL_MODEL_MARK")
    WALL_USER_HEIGHT_PARAM = _Enum("WALL_USER_HEIGHT_PARAM")


class BuiltInCategory(object):
    OST_Walls = _Enum("OST_Walls")
    OST_Levels = _Enum("OST_Levels")
    OST_Doors = _Enum("OST_Doors")
    OST_Windows = _Enum("OST_Windows")


class _Opciones(object):
    def SetForcedModalHandling(self, valor):
        self.modal = valor

    def SetClearAfterRollback(self, valor):
        self.clear = valor

    def SetFailuresPreprocessor(self, pre):
        self.pre = pre


class FailureMessageFalso(object):
    """Mensaje de fallo de Revit simulado (FailureMessageAccessor)."""

    def __init__(self, texto, severidad=None, elementos=()):
        self.texto = texto
        self.severidad = severidad or FailureSeverity.Warning
        self.elementos = [ElementId(e) for e in elementos]

    def GetSeverity(self):
        return self.severidad

    def GetDescriptionText(self):
        return self.texto

    def GetFailingElementIds(self):
        return list(self.elementos)


class FailuresAccessorFalso(object):
    """Lo minimo de FailuresAccessor que usa utils._FailureSwallower."""

    def __init__(self, mensajes):
        self.mensajes = list(mensajes)
        self.avisos_borrados = False

    def GetFailureMessages(self):
        return list(self.mensajes)

    def DeleteAllWarnings(self):
        self.avisos_borrados = True
        self.mensajes = [m for m in self.mensajes if m.GetSeverity() != FailureSeverity.Warning]


class Transaction(object):
    """Registra Start/Commit/RollBack. `resultado_commit` permite simular un fallo.

    `fallos` (lista de FailureMessageFalso) simula lo que Revit comunica al
    preprocesador de fallos al confirmar: Commit se lo pasa al preprocesador
    registrado con SetFailuresPreprocessor y, si este pide revertir, el estado
    final es RolledBack. `al_confirmar` (callable) simula lo que Revit hace al
    regenerar (por ejemplo, borrar un hueco que no corta su muro)."""

    creadas = []
    resultado_commit = TransactionStatus.Committed
    fallos = []
    al_confirmar = None

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
        preprocesador = getattr(self.opciones, "pre", None)
        if Transaction.fallos and preprocesador is not None:
            accesor = FailuresAccessorFalso(Transaction.fallos)
            if preprocesador.PreprocessFailures(accesor) == FailureProcessingResult.ProceedWithRollBack:
                self.estado = TransactionStatus.RolledBack
        if self.estado == TransactionStatus.Committed and Transaction.al_confirmar is not None:
            Transaction.al_confirmar(self)
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


class Element(object):
    pass


class FilteredElementCollector(object):
    def __init__(self, doc, view_id=None):
        self.doc = doc

    def OfClass(self, clase):
        return self

    def OfCategory(self, categoria):
        return self

    def WhereElementIsNotElementType(self):
        return self

    def WhereElementIsElementType(self):
        return self

    def WherePasses(self, filtro):
        return self

    def ToElements(self):
        return []

    def ToElementIds(self):
        return []

    def GetElementCount(self):
        return 0

    def __iter__(self):
        return iter([])


class Line(object):
    """Linea acotada con lo que usa estructural._situar_en_muro."""

    def __init__(self, a=None, b=None):
        self.a = a or XYZ(0, 0, 0)
        self.b = b or XYZ(0, 0, 0)

    @staticmethod
    def CreateBound(a, b):
        return Line(a, b)

    def GetEndPoint(self, indice):
        return self.a if indice == 0 else self.b

    @property
    def Length(self):
        return self.a.DistanceTo(self.b)


class CurveArray(object):
    def __init__(self):
        self.curvas = []

    def Append(self, curva):
        self.curvas.append(curva)


class BoundingBoxXYZ(object):
    def __init__(self, minimo, maximo):
        self.Min = minimo
        self.Max = maximo


class LocationCurve(object):
    def __init__(self, curva):
        self.Curve = curva


class LocationPoint(object):
    def __init__(self, punto, rotacion=0.0):
        self.Point = punto
        self.Rotation = rotacion


class Wall(Element):
    pass


class Floor(Element):
    pass


class RoofBase(Element):
    pass


class SpecTypeId(object):
    Length = _Enum("Length")
    Area = _Enum("Area")
    Volume = _Enum("Volume")
    Angle = _Enum("Angle")


class StorageType(object):
    None_ = _Enum("None")
    Integer = _Enum("Integer")
    Double = _Enum("Double")
    String = _Enum("String")
    ElementId = _Enum("ElementId")


class LabelUtils(object):
    @staticmethod
    def GetLabelForGroup(group_id):
        return "Group"
