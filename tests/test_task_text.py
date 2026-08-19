from __future__ import annotations

from vikunjbot.database import HookView
from vikunjbot.event_worker import _merge_event_bucket, _needs_bucket_enrichment
from vikunjbot.task_fields import TaskDisplayField
from vikunjbot.task_text import (
    render_deleted_task,
    render_filtered_task,
    render_task,
    task_snapshot,
)


def test_zero_due_date_is_not_rendered_as_a_deadline() -> None:
    rendered = render_task(
        {
            "title": "No deadline",
            "due_date": "0001-01-01T00:00:00Z",
        }
    )

    assert "Due:" not in rendered


def test_actual_due_date_is_rendered() -> None:
    rendered = render_task(
        {
            "title": "Scheduled",
            "due_date": "2026-08-19T12:30:00+00:00",
        }
    )

    assert "🗓 Due: 2026-08-19 12:30:00 UTC" in rendered


def test_task_fields_can_be_hidden_independently_while_title_remains() -> None:
    rendered = render_task(
        {
            "identifier": "DEMO-42",
            "title": "Minimal task",
            "done": True,
            "bucket": {"title": "Done"},
            "labels": [{"title": "important"}],
        },
        display_fields=frozenset({TaskDisplayField.STATUS}),
    )

    assert rendered == "<b>Minimal task</b>\n✅ Completed"


def test_bucket_title_from_expanded_bucket_map_is_forwarded() -> None:
    task = {
        "title": "Move me",
        "bucket_id": 8,
        "buckets": {"8": {"id": 8, "title": "Ready for review"}},
    }

    assert task_snapshot(task)["bucket"] == "Ready for review"
    assert _needs_bucket_enrichment(task) is False


def test_single_bucket_from_a_webhook_event_is_forwarded_without_a_bucket_id() -> None:
    task = {
        "title": "Move me",
        "bucket_id": 0,
        "buckets": [{"id": 8, "title": "Ready for review", "project_view_id": 12}],
    }

    assert task_snapshot(task)["bucket"] == "Ready for review"
    assert _needs_bucket_enrichment(task) is False


def test_incomplete_bucket_requests_service_account_enrichment() -> None:
    assert _needs_bucket_enrichment({"bucket_id": 8, "bucket": {"id": 8}}) is True


def test_enrichment_keeps_the_kanban_bucket_from_the_webhook_event() -> None:
    enriched = _merge_event_bucket(
        {"bucket_id": 8},
        {
            "bucket_id": 0,
            "buckets": [
                {"id": 7, "title": "Backlog"},
                {"id": 8, "title": "Ready for review"},
            ],
        },
    )

    assert task_snapshot(enriched)["bucket"] == "Ready for review"


def test_enrichment_keeps_the_event_bucket_when_the_task_has_already_moved() -> None:
    enriched = _merge_event_bucket(
        {"bucket_id": 0, "buckets": [{"id": 10, "title": "Review"}]},
        {"bucket_id": 0, "buckets": [{"id": 12, "title": "Iter 2, done"}]},
    )

    assert task_snapshot(enriched)["bucket"] == "Review"


def test_ambiguous_buckets_without_a_bucket_id_are_not_guessed() -> None:
    task = {
        "bucket_id": 0,
        "buckets": [{"id": 10, "title": "Review"}, {"id": 12, "title": "Done"}],
    }

    assert task_snapshot(task)["bucket"] == ""


def test_unknown_bucket_is_not_presented_as_an_id() -> None:
    rendered = render_task({"title": "Move me", "bucket_id": 8})

    assert "Bucket:" not in rendered


def test_selected_kanban_views_render_their_own_current_bucket_names() -> None:
    rendered = render_task(
        {
            "title": "Move me",
            "buckets": [
                {"project_view_id": 7, "title": "In progress"},
                {"project_view_id": 9, "title": "Ready to publish"},
            ],
        },
        (HookView(7, "Engineering"), HookView(9, "Release")),
    )

    assert "📥 Engineering: In progress" in rendered
    assert "📥 Release: Ready to publish" in rendered


def test_deleted_task_is_rendered_as_a_terminal_notice() -> None:
    assert render_deleted_task({"identifier": "DEMO-42", "title": "Gone"}) == (
        "<b>DEMO-42: Gone</b>\n🗑 Deleted"
    )


def test_filtered_task_strikes_every_displayed_field_and_escapes_vikunja_data() -> None:
    task = {
        "identifier": "<DEMO&42>",
        "title": "Close </s><b>escape",
        "done": False,
        "bucket": "R&D <review>",
        "labels": ["<urgent>"],
        "assignees": ["alice&bob"],
    }

    rendered = render_filtered_task(task)

    assert rendered.startswith("<s><b>&lt;DEMO&amp;42&gt;: Close &lt;/s&gt;&lt;b&gt;escape</b>")
    assert "📥 Bucket: R&amp;D &lt;review&gt;" in rendered
    assert "🏷 &lt;urgent&gt;" in rendered
    assert "👤 alice&amp;bob" in rendered
    assert rendered.endswith(
        "</s>\n<i>This task is no longer visible in the selected delivery views.</i>"
    )
    assert "</s><b>escape" not in rendered


def test_task_snapshot_is_idempotent_for_terminal_rendering() -> None:
    snapshot = {
        "title": "Stored",
        "identifier": "DEMO-42",
        "done": True,
        "due_date": "2026-08-19T12:30:00+00:00",
        "bucket": "Done",
        "view_buckets": [("Board", "Done")],
        "labels": ["important"],
        "assignees": ["alex"],
    }

    assert task_snapshot(snapshot) == snapshot
