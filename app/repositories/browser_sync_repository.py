from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import BrowserSyncEvent


class BrowserSyncRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_client_record_id(self, client_record_id: str) -> Optional[BrowserSyncEvent]:
        return self.db.query(BrowserSyncEvent).filter(BrowserSyncEvent.client_record_id == client_record_id).first()

    def find_duplicate_entity_event(self, *, entity_type: str, zentao_bug_id: str | None, zentao_case_id: str | None, exclude_event_id: int | None = None) -> Optional[BrowserSyncEvent]:
        query = self.db.query(BrowserSyncEvent).filter(BrowserSyncEvent.entity_type == entity_type)
        if exclude_event_id:
            query = query.filter(BrowserSyncEvent.id != exclude_event_id)
        if entity_type == "bug" and zentao_bug_id:
            return query.filter(BrowserSyncEvent.zentao_bug_id == zentao_bug_id).order_by(BrowserSyncEvent.id.desc()).first()
        if entity_type == "testcase" and zentao_case_id:
            return query.filter(BrowserSyncEvent.zentao_case_id == zentao_case_id).order_by(BrowserSyncEvent.id.desc()).first()
        return None

    def create_event(self, **kwargs) -> BrowserSyncEvent:
        row = BrowserSyncEvent(**kwargs)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def save(self, row: BrowserSyncEvent) -> BrowserSyncEvent:
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def list_events(
        self,
        *,
        entity_type: str | None = None,
        status: str | None = None,
        display_bucket: str | None = None,
        source_type: str | None = None,
        keyword: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        only_unapplied: bool = False,
        product_name_filter: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        query = self.db.query(BrowserSyncEvent)

        if entity_type:
            query = query.filter(BrowserSyncEvent.entity_type == entity_type)
        if status:
            query = query.filter(BrowserSyncEvent.status == status)
        if display_bucket:
            query = query.filter(BrowserSyncEvent.display_bucket == display_bucket)
        if source_type:
            query = query.filter(BrowserSyncEvent.source_type == source_type)
        if date_from:
            query = query.filter(BrowserSyncEvent.created_at >= date_from)
        if date_to:
            query = query.filter(BrowserSyncEvent.created_at <= date_to)
        if only_unapplied:
            query = query.filter(BrowserSyncEvent.status != "applied")
        if product_name_filter:
            query = query.filter(BrowserSyncEvent.zentao_product_name.like(f"%{product_name_filter}%"))
        if keyword:
            kw = f"%{keyword.strip()}%"
            query = query.filter(
                or_(
                    BrowserSyncEvent.creator_name.like(kw),
                    BrowserSyncEvent.operator_name.like(kw),
                    BrowserSyncEvent.zentao_bug_id.like(kw),
                    BrowserSyncEvent.zentao_case_id.like(kw),
                    BrowserSyncEvent.zentao_req_id.like(kw),
                    BrowserSyncEvent.zentao_requirement_name.like(kw),
                    BrowserSyncEvent.zentao_bug_title.like(kw),
                    BrowserSyncEvent.zentao_case_title.like(kw),
                    BrowserSyncEvent.linked_case_id.like(kw),
                    BrowserSyncEvent.linked_case_label.like(kw),
                    BrowserSyncEvent.push_message.like(kw),
                )
            )

        total = query.count()
        items = (
            query.order_by(BrowserSyncEvent.created_at.desc(), BrowserSyncEvent.id.desc())
            .offset(max(0, (page - 1) * page_size))
            .limit(page_size)
            .all()
        )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def get_event(self, event_id: int) -> Optional[BrowserSyncEvent]:
        return self.db.query(BrowserSyncEvent).filter(BrowserSyncEvent.id == event_id).first()
