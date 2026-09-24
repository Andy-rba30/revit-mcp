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
    FAMILY_LEVEL_PARAM = _Enum("FAMILY_LEVEL_PARAM")
    LEVEL_PARAM = _Enum("LEVEL_PARAM")
    SCHEDULE_LEVEL_PARAM = _Enum("SCHEDULE_LEVEL_PARAM")
    FAMILY_BASE_LEVEL_PARAM = _Enum("FAMILY_BASE_LEVEL_PARAM")
    ELEM_PARTITION_PARAM = _Enum("ELEM_PARTITION_PARAM")


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
    @staticmethod
    def CreateBound(a, b):
        return Line()


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
