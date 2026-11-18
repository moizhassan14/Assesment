from __future__ import annotations

from flask import Blueprint, request

from app.api.common import idempotency_key, int_arg, ok, parse_body, services
from app.schemas.api import BrandCreate, BrandUpdate, RunCreate
from app.services.errors import ApiError

bp = Blueprint("brands", __name__, url_prefix="/api/brands")


@bp.post("")
def create_brand():  # type: ignore[no-untyped-def]
    body = parse_body(BrandCreate)
    brand = services().brands.create(body)
    return ok(brand, 201, headers={"Location": f"/api/brands/{brand['id']}"})


@bp.get("")
def list_brands():  # type: ignore[no-untyped-def]
    limit = int_arg("limit", 20, 1, 100)
    offset = int_arg("offset", 0, 0, 1_000_000)
    q = (request.args.get("q") or "").strip()[:80] or None
    items, total = services().brands.list(limit, offset, q)
    return ok(items, meta={"total": total, "limit": limit, "offset": offset})


@bp.get("/<brand_id>")
def get_brand(brand_id: str):  # type: ignore[no-untyped-def]
    return ok(services().brands.get(brand_id))


@bp.patch("/<brand_id>")
def update_brand(brand_id: str):  # type: ignore[no-untyped-def]
    raw = request.get_json(silent=True)
    if isinstance(raw, dict):
        immutable = [f for f in ("domain", "market") if f in raw]
        if immutable:
            raise ApiError(422, "validation_error", "Request body failed validation.", details=[
                {"field": f, "message": "is immutable; create a new brand instead", "type": "immutable_field"} for f in immutable])
    body = parse_body(BrandUpdate)
    return ok(services().brands.update(brand_id, body))


@bp.post("/<brand_id>/runs")
def create_run(brand_id: str):  # type: ignore[no-untyped-def]
    key = idempotency_key()
    if not request.get_data(cache=True).strip() and not request.is_json:
        body = RunCreate()
    else:
        body = parse_body(RunCreate)
    payload, replayed = services().runs.create(brand_id, body, key)
    headers = {"Location": payload["status_url"]}
    if replayed:
        headers["Idempotent-Replayed"] = "true"
    return ok(payload, 202, headers=headers)


@bp.get("/<brand_id>/runs")
def list_runs(brand_id: str):  # type: ignore[no-untyped-def]
    limit = int_arg("limit", 20, 1, 100)
    offset = int_arg("offset", 0, 0, 1_000_000)
    status = request.args.get("status")
    if status and status not in ("queued", "running", "success", "partial_success", "failed"):
        raise ApiError(400, "invalid_query_parameter", "Unknown status filter.",
                       details=[{"field": "status", "message": "unknown status", "type": "enum"}])
    items, total = services().runs.list_for_brand(brand_id, limit, offset, status)
    return ok(items, meta={"total": total, "limit": limit, "offset": offset})


@bp.get("/<brand_id>/compare")
def compare(brand_id: str):  # type: ignore[no-untyped-def]
    data = services().reports.compare(brand_id, request.args.get("base_run_id"), request.args.get("target_run_id"))
    return ok(data)

