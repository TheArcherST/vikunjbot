from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError

from vikunjbot.database import Database
from vikunjbot.routing import InvalidRouteTag, parse_hook_id
from vikunjbot.settings import Settings, settings

logger = logging.getLogger(__name__)


def create_app(config: Settings = settings, database: Database | None = None) -> FastAPI:
    database = database or Database(config.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await database.dispose()

    app = FastAPI(title="vikunjbot event relay", lifespan=lifespan)
    app.state.database = database
    app.state.settings = config

    @app.get("/healthz", status_code=status.HTTP_200_OK)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/events")
    @app.post("/events/{route_tag:path}")
    async def receive_event(request: Request, route_tag: str = "") -> Response:
        content_length = request.headers.get("content-length")
        if content_length and _content_length_exceeds_limit(
            content_length, config.relay_max_body_bytes
        ):
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="payload too large"
            )
        raw_body = await request.body()
        if len(raw_body) > config.relay_max_body_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="payload too large"
            )
        payload = _validate_payload(raw_body)
        if not route_tag:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="the webhook URL must contain a hook UUID",
            )
        try:
            hook_id = parse_hook_id(route_tag)
        except InvalidRouteTag as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"invalid route tag: {exc}",
            ) from exc
        try:
            active_hook = await database.get_active_hook(hook_id)
            if active_hook is None:
                known_hook = await database.get_hook(hook_id)
                if known_hook is not None and known_hook.deleted_at is not None:
                    logger.info("Discarding delivery for deleted hook %s", hook_id)
                    return Response(
                        content=json.dumps({"accepted": False, "deleted": True}),
                        status_code=status.HTTP_202_ACCEPTED,
                        media_type="application/json",
                    )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="unknown or inactive hook",
                )
            event_id, created = await database.enqueue_event(hook_id, raw_body, payload)
        except SQLAlchemyError as exc:
            # Never report acceptance when the durable write failed. Vikunja can
            # retry the webhook after a database outage.
            logger.exception("Could not persist webhook delivery")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="relay storage unavailable"
            ) from exc
        response = {"id": event_id, "accepted": created}
        return Response(
            content=json.dumps(response),
            status_code=status.HTTP_202_ACCEPTED,
            media_type="application/json",
        )

    return app


def _validate_payload(raw_body: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="body must be valid JSON"
        ) from exc
    if not isinstance(decoded, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="payload must be an object"
        )
    if not isinstance(decoded.get("event_name"), str) or not decoded["event_name"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="event_name is required"
        )
    if not isinstance(decoded.get("data"), dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="data is required"
        )
    try:
        from vikunjbot.timeutils import parse_event_time

        parse_event_time(decoded.get("time"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="time must be an ISO-8601 value with a timezone",
        ) from exc
    return decoded


def _content_length_exceeds_limit(value: str, limit: int) -> bool:
    try:
        return int(value) > limit
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content-Length must be an integer",
        ) from exc


def main() -> None:
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    uvicorn.run(create_app(), host=settings.relay_host, port=settings.relay_port)
