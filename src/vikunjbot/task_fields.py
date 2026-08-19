from __future__ import annotations

from enum import StrEnum


class TaskDisplayField(StrEnum):
    IDENTIFIER = "identifier"
    STATUS = "status"
    BUCKET = "bucket"
    DUE_DATE = "due_date"
    LABELS = "labels"
    ASSIGNEES = "assignees"


ALL_TASK_DISPLAY_FIELDS = frozenset(TaskDisplayField)


TASK_DISPLAY_FIELD_LABELS = {
    TaskDisplayField.IDENTIFIER: "Task ID",
    TaskDisplayField.STATUS: "Status",
    TaskDisplayField.BUCKET: "Bucket",
    TaskDisplayField.DUE_DATE: "Due date",
    TaskDisplayField.LABELS: "Labels",
    TaskDisplayField.ASSIGNEES: "Assignees",
}
