from __future__ import annotations

from flask import Blueprint, Response, request

from app.api.common import int_arg, ok, services
from app.services.errors import ApiError

bp = Blueprint("runs", __name__, url_prefix="/api/runs")


@bp.get("/<run_id>")
def get_run(run_id: str):  # type: ignore[no-untyped-def]
    return ok(services().runs.get(run_id))


@bp.get("/<run_id>/events")
def get_events(run_id: str):  # type: ignore[no-untyped-def]
    after = int_arg("after_seq", 0, 0, 10_000_000)
    limit = int_arg("limit", 200, 1, 500)
    data, total, next_seq = services().runs.events(run_id, after, limit)
    return ok(data, meta={"total": total, "limit": limit, "offset": 0, "next_after_seq": next_seq})


@bp.get("/<run_id>/report")
def get_report(run_id: str):  # type: ignore[no-untyped-def]
    fmt = request.args.get("format", "json")
    if fmt == "csv":
        body = services().reports.report_csv(run_id)
        return Response(body, mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="run-{run_id[:8]}-opportunities.csv"'})
    if fmt != "json":
        raise ApiError(400, "invalid_query_parameter", "format must be 'json' or 'csv'.",
                       details=[{"field": "format", "message": "must be json or csv", "type": "enum"}])
    return ok(services().reports.report(run_id))
