from enum import StrEnum


class ProjectViewKind(StrEnum):
    LIST = "list"
    TABLE = "table"
    GANTT = "gantt"
    KANBAN = "kanban"
