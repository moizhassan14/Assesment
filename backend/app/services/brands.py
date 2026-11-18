from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.extensions import Database
from app.models import AnalysisRun, Brand, Competitor
from app.schemas.api import BrandCreate, BrandUpdate
from app.services.errors import ApiError, not_found
from app.services.serializers import brand_json
from app.utils.time import utcnow


def _latest_runs(s: Session, brand_ids: list[str]) -> tuple[dict[str, AnalysisRun], dict[str, int]]:
    if not brand_ids:
        return {}, {}
    runs = s.scalars(
        select(AnalysisRun).where(AnalysisRun.brand_id.in_(brand_ids)).order_by(AnalysisRun.created_at.desc())
    ).all()
    latest: dict[str, AnalysisRun] = {}
    counts: dict[str, int] = {}
    for r in runs:
        latest.setdefault(r.brand_id, r)
        counts[r.brand_id] = counts.get(r.brand_id, 0) + 1
    return latest, counts


class BrandService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, body: BrandCreate) -> dict[str, Any]:
        with self.db.session() as s:
            existing = s.scalar(select(Brand).where(Brand.domain == body.domain))
            if existing is not None:
                raise ApiError(409, "duplicate_brand", f"A brand for {body.domain} already exists.",
                               details=[{"field": "domain", "message": "Domain already registered", "type": "duplicate"}],
                               meta={"existing_id": existing.id})
            brand = Brand(
                name=body.name, domain=body.domain, description=body.description,
                location_code=body.market.location_code, language_code=body.market.language_code,
                seed_keywords=body.seed_keywords,
            )
            brand.competitors = [Competitor(name=c.name, domain=c.domain, position=i) for i, c in enumerate(body.competitors)]
            s.add(brand)
            s.flush()
            return brand_json(brand, None, 0)

    def list(self, limit: int, offset: int, q: str | None) -> tuple[list[dict[str, Any]], int]:
        with self.db.session() as s:
            stmt = select(Brand).options(selectinload(Brand.competitors))
            count_stmt = select(func.count()).select_from(Brand)
            if q:
                like = f"%{q.lower()}%"
                cond = or_(func.lower(Brand.name).like(like), func.lower(Brand.domain).like(like))
                stmt = stmt.where(cond)
                count_stmt = count_stmt.where(cond)
            total = int(s.scalar(count_stmt) or 0)
            brands = s.scalars(stmt.order_by(Brand.updated_at.desc()).limit(limit).offset(offset)).all()
            latest, counts = _latest_runs(s, [b.id for b in brands])
            return [brand_json(b, latest.get(b.id), counts.get(b.id, 0)) for b in brands], total

    def get(self, brand_id: str) -> dict[str, Any]:
        with self.db.session() as s:
            brand = s.get(Brand, brand_id, options=[selectinload(Brand.competitors)])
            if brand is None:
                raise not_found("Brand")
            latest, counts = _latest_runs(s, [brand.id])
            return brand_json(brand, latest.get(brand.id), counts.get(brand.id, 0))

    def update(self, brand_id: str, body: BrandUpdate) -> dict[str, Any]:
        with self.db.session() as s:
            brand = s.get(Brand, brand_id, options=[selectinload(Brand.competitors)])
            if brand is None:
                raise not_found("Brand")
            if body.name is not None:
                brand.name = body.name
            if "description" in body.model_fields_set:
                brand.description = body.description
            if body.seed_keywords is not None:
                brand.seed_keywords = body.seed_keywords
            if body.competitors is not None:
                if brand.domain in {c.domain for c in body.competitors}:
                    raise ApiError(422, "validation_error", "Request body failed validation.", details=[
                        {"field": "competitors", "message": "a competitor cannot have the brand's own domain", "type": "value_error"}])
                brand.competitors.clear()
                s.flush()
                brand.competitors.extend(Competitor(name=c.name, domain=c.domain, position=i) for i, c in enumerate(body.competitors))
            brand.updated_at = utcnow()
            s.flush()
            latest, counts = _latest_runs(s, [brand.id])
            return brand_json(brand, latest.get(brand.id), counts.get(brand.id, 0))
