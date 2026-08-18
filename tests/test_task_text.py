from __future__ import annotations

from vikunjbot.event_worker import _merge_event_bucket, _needs_bucket_enrichment
from vikunjbot.task_text import render_task, task_snapshot


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
