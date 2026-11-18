"""Report assembly (read path) and run comparison."""

from __future__ import annotations

import csv
import io
from typing import Any

from sqlalchemy import select

from app.agents.report import scoring_meta
from app.extensions import Database
from app.models import AnalysisRun, Brand, Observation, Opportunity, Recommendation, ResearchQuery
from app.services.errors import ApiError, not_found
from app.services.serializers import (
    observation_json,
    opportunity_json,
    query_json,
    recommendation_json,
    run_summary_json,
    snapshot_as_brand,
)
from app.utils.time import iso

TERMINAL = ("success", "partial_success", "failed")
REPORTABLE = ("success", "partial_success")
_PRIORITY = {"high": 0, "medium": 1, "low": 2}


class ReportService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def report(self, run_id: str) -> dict[str, Any]:
        with self.db.session() as s:
            run = s.get(AnalysisRun, run_id)
            if run is None:
                raise not_found("Run")
            queries = s.scalars(select(ResearchQuery).where(ResearchQuery.run_id == run_id).order_by(ResearchQuery.position)).all()
            qmap = {q.id: q for q in queries}
            observations = s.scalars(select(Observation).where(Observation.run_id == run_id)).all()
            opps = s.scalars(select(Opportunity).where(Opportunity.run_id == run_id)).all()
            recs = s.scalars(select(Recommendation).where(Recommendation.run_id == run_id).order_by(Recommendation.position)).all()

            opps_sorted = sorted(opps, key=lambda o: (o.score is None, -(o.score or 0.0), o.rank or 10_000))
            opp_rank = {o.id: (o.rank or 10_000) for o in opps}
            opp_query = {o.id: qmap.get(o.query_id) for o in opps}
            recs_sorted = sorted(recs, key=lambda r: (_PRIORITY.get(r.priority, 3), opp_rank.get(r.opportunity_id, 10_000)))
            surface_order = {"chatgpt": 0, "google_organic": 1, "google_ai_overview": 2}
            obs_sorted = sorted(
                observations,
                key=lambda o: (qmap[o.query_id].position if o.query_id in qmap else 999, surface_order.get(o.surface, 9)),
            )
            payload = run.report or {}
            queries_sorted = sorted(queries, key=lambda q: (not q.selected_for_measurement, q.position))
            return {
                "run": run_summary_json(run),
                "is_final": run.status in TERMINAL,
            "data_mode": (run.config or {}).get("data_mode", "live"),
                "brand_snapshot": snapshot_as_brand(run.brand_snapshot or {}),
                "summary": payload.get("summary"),
                "visibility": payload.get("visibility"),
                "opportunities": [opportunity_json(o, qmap.get(o.query_id)) for o in opps_sorted],
                "recommendations": [
                    recommendation_json(r, (opp_query.get(r.opportunity_id).text if opp_query.get(r.opportunity_id) else None))
                    for r in recs_sorted
                ],
                "queries": [query_json(q) for q in queries_sorted],
                "observations": [observation_json(o, qmap[o.query_id].text if o.query_id in qmap else "") for o in obs_sorted],
                "errors": list(run.errors or []),
                "scoring": payload.get("scoring") or scoring_meta(),
                "plan": payload.get("plan"),
                "generated_at": iso(run.finished_at),
            }

    def report_csv(self, run_id: str) -> str:
        report = self.report(run_id)
        obs_by_query: dict[str, dict[str, dict[str, Any]]] = {}
        for o in report["observations"]:
            obs_by_query.setdefault(o["query_id"], {})[o["surface"]] = o
        rec_by_opp = {r["opportunity_id"]: r for r in report["recommendations"]}
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["rank", "query", "score", "confidence", "coverage", "search_volume", "paid_competition",
                         "keyword_difficulty", "chatgpt_brand_mentioned", "chatgpt_brand_cited", "organic_rank",
                         "ai_overview_brand_cited", "recommendation_title"])

        def cell(obs: dict[str, Any] | None, key: str) -> Any:
            if obs is None or obs["status"] != "measured":
                return "unknown" if obs is None or obs["status"] == "failed" else ""
            return obs["brand"].get(key)

        for o in report["opportunities"]:
            m = o["metrics"] or {}
            obs = obs_by_query.get(o["query_id"], {})
            rec = rec_by_opp.get(o["id"])
            writer.writerow([
                o["rank"] if o["rank"] is not None else "", o["query"], o["score"] if o["score"] is not None else "",
                o["confidence"], o["coverage"], m.get("search_volume", ""), m.get("paid_competition", ""),
                m.get("keyword_difficulty", ""), cell(obs.get("chatgpt"), "mentioned"), cell(obs.get("chatgpt"), "cited"),
                cell(obs.get("google_organic"), "rank"), cell(obs.get("google_ai_overview"), "cited"),
                rec["title"] if rec else "",
            ])
        return buf.getvalue()

    # ------------------------------------------------------------------ comparison
    def compare(self, brand_id: str, base_run_id: str | None, target_run_id: str | None) -> dict[str, Any]:
        with self.db.session() as s:
            if s.get(Brand, brand_id) is None:
                raise not_found("Brand")
            finished = s.scalars(
                select(AnalysisRun).where(AnalysisRun.brand_id == brand_id, AnalysisRun.status.in_(REPORTABLE))
                .order_by(AnalysisRun.created_at.desc())
            ).all()
            by_id = {r.id: r for r in finished}

            def resolve(run_id: str | None, label: str) -> AnalysisRun | None:
                if run_id is None:
                    return None
                run = s.get(AnalysisRun, run_id)
                if run is None:
                    raise not_found(f"{label} run")
                if run.brand_id != brand_id:
                    raise ApiError(409, "not_comparable", f"The {label} run belongs to a different brand.")
                if run.id not in by_id:
                    raise ApiError(409, "not_comparable", f"The {label} run has no finished report yet.")
                return run

            target = resolve(target_run_id, "target") or (finished[0] if finished else None)
            base = resolve(base_run_id, "base")
            def mode(r: AnalysisRun) -> str:
                return (r.config or {}).get("data_mode", "live")

            if base is None and target is not None:
                base = next((r for r in finished if r.created_at < target.created_at and mode(r) == mode(target)), None)
            if base is not None and target is not None and mode(base) != mode(target):
                raise ApiError(409, "not_comparable", "Cannot compare a sandbox run with a live run.")
            if target is None or base is None or base.id == target.id:
                raise ApiError(409, "not_comparable", "At least two finished runs are needed to compare results.")
            if base.created_at > target.created_at:
                base, target = target, base
            base_rows = self._query_rows(s, base.id)
            target_rows = self._query_rows(s, target.id)
            return self._diff(brand_id, base, target, base_rows, target_rows)

    @staticmethod
    def _query_rows(s, run_id: str) -> dict[str, dict[str, Any]]:  # type: ignore[no-untyped-def]
        queries = s.scalars(select(ResearchQuery).where(ResearchQuery.run_id == run_id,
                                                        ResearchQuery.selected_for_measurement.is_(True))).all()
        obs = s.scalars(select(Observation).where(Observation.run_id == run_id)).all()
        opps = {o.query_id: o for o in s.scalars(select(Opportunity).where(Opportunity.run_id == run_id)).all()}
        rows: dict[str, dict[str, Any]] = {}
        for q in queries:
            mine = {o.surface: o for o in obs if o.query_id == q.id}

            def val(surface: str, key: str) -> Any:
                o = mine.get(surface)
                if o is None or o.status != "measured":
                    return None
                return (o.brand or {}).get(key)

            organic = mine.get("google_organic")
            rows[q.normalized_text] = {
                "score": opps[q.id].score if q.id in opps else None,
                "chatgpt_mentioned": val("chatgpt", "mentioned"),
                "chatgpt_cited": val("chatgpt", "cited"),
                "organic_rank": (organic.brand or {}).get("rank") if organic is not None and organic.status == "measured" else None,
                "organic_measured": organic is not None and organic.status == "measured",
                "ai_overview_cited": val("google_ai_overview", "cited"),
                "search_volume": (q.metrics or {}).get("search_volume") if q.metrics else None,
            }
        return rows

    @staticmethod
    def _diff(brand_id: str, base: AnalysisRun, target: AnalysisRun, b: dict[str, dict[str, Any]],
              t: dict[str, dict[str, Any]]) -> dict[str, Any]:
        summary = {"improved": 0, "declined": 0, "unchanged": 0, "new": 0, "dropped": 0, "not_comparable": 0}
        out = []
        for query in list(dict.fromkeys(list(t) + list(b))):
            bs, ts = b.get(query), t.get(query)
            notes: list[str] = []
            if bs is None:
                status = "new"
            elif ts is None:
                status = "dropped"
            else:
                signals: list[int] = []
                for key, label in (("chatgpt_mentioned", "mentioned by ChatGPT"), ("chatgpt_cited", "cited by ChatGPT"),
                                   ("ai_overview_cited", "cited in Google AI Overview")):
                    if bs[key] is not None and ts[key] is not None and bs[key] != ts[key]:
                        signals.append(1 if ts[key] else -1)
                        notes.append(("Now " if ts[key] else "No longer ") + label)
                if bs["organic_measured"] and ts["organic_measured"]:
                    br, tr = bs["organic_rank"], ts["organic_rank"]
                    if br != tr:
                        better = (tr is not None) and (br is None or tr < br)
                        signals.append(1 if better else -1)
                        notes.append(f"Organic rank {('#' + str(br)) if br else 'not ranked'} → {('#' + str(tr)) if tr else 'not ranked'}")
                comparable = any(bs[k] is not None and ts[k] is not None for k in ("chatgpt_mentioned", "ai_overview_cited")) or (
                    bs["organic_measured"] and ts["organic_measured"])
                if not comparable:
                    status = "not_comparable"
                elif not signals:
                    status = "unchanged"
                else:
                    status = "improved" if sum(signals) > 0 else "declined" if sum(signals) < 0 else "unchanged"
            summary[status] += 1
            delta_score = (round(ts["score"] - bs["score"], 1) if bs and ts and bs["score"] is not None and ts["score"] is not None else None)
            delta_rank = (bs["organic_rank"] - ts["organic_rank"] if bs and ts and bs["organic_rank"] and ts["organic_rank"] else None)

            def public(row: dict[str, Any] | None) -> dict[str, Any] | None:
                if row is None:
                    return None
                return {k: row[k] for k in ("score", "chatgpt_mentioned", "chatgpt_cited", "organic_rank", "ai_overview_cited", "search_volume")}

            out.append({"query": query, "status": status, "base": public(bs), "target": public(ts),
                        "delta": {"score": delta_score, "organic_rank": delta_rank}, "notes": notes})

        def rate(rows: dict[str, dict[str, Any]], key: str, top10: bool = False) -> float | None:
            if top10:
                measured = [r for r in rows.values() if r["organic_measured"]]
                return (sum(1 for r in measured if r["organic_rank"] and r["organic_rank"] <= 10) / len(measured)) if measured else None
            measured = [r for r in rows.values() if r[key] is not None]
            return (sum(1 for r in measured if r[key]) / len(measured)) if measured else None

        def delta(x: float | None, y: float | None) -> float | None:
            return round(y - x, 4) if x is not None and y is not None else None

        b_scores = [r["score"] for r in b.values() if r["score"] is not None]
        t_scores = [r["score"] for r in t.values() if r["score"] is not None]
        summary.update({
            "ai_visibility_rate_delta": delta(rate(b, "chatgpt_mentioned"), rate(t, "chatgpt_mentioned")),
            "organic_top10_rate_delta": delta(rate(b, "", True), rate(t, "", True)),
            "avg_score_delta": (round(sum(t_scores) / len(t_scores) - sum(b_scores) / len(b_scores), 1)
                                if b_scores and t_scores else None),
        })
        return {"brand_id": brand_id, "base_run": run_summary_json(base), "target_run": run_summary_json(target),
                "summary": summary, "queries": out}
