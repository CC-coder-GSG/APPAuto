from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models import Requirement, User, Version
from app.services.workbench_link_service import WorkbenchLinkService


class WorkbenchService:
    def __init__(self, db: Session):
        self.db = db
        self.link_service = WorkbenchLinkService(db)

    def get_my_workbench(
        self,
        *,
        current_user: User,
        major_version_id: int | None = None,
        mode: str = "version",
        software_id: int | None = None,
    ) -> list[dict]:
        query = (
            self.db.query(Requirement)
            .options(
                joinedload(Requirement.major_version),
                joinedload(Requirement.test_cases),
                joinedload(Requirement.test_notes_updated_by),
            )
            .filter(Requirement.owner_id == current_user.id)
        )

        if mode == "version" and major_version_id:
            query = query.filter(Requirement.major_version_id == major_version_id)
        elif mode == "all_pending":
            query = query.filter(Requirement.test_completed.is_(False))
        if software_id:
            query = query.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)

        reqs = query.order_by(Requirement.id.desc()).all()
        minors = self.link_service.minor_version_name_map()
        case_view_map = self.link_service.build_requirement_case_view(reqs, minors)
        free_bug_map, auto_story_bug_map = self.link_service.build_requirement_free_bug_view(
            reqs,
            minors,
            include_retest=False,
        )

        return [
            {
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "case_completed": r.case_completed,
                "test_completed": r.test_completed,
                "major_version_id": r.major_version_id,
                "major_version_name": r.major_version.version_no if r.major_version else "",
                "zentao_story_id": r.zentao_story_id,
                "test_notes": r.test_notes,
                "test_notes_updated_at": r.test_notes_updated_at.isoformat() if r.test_notes_updated_at else None,
                "test_notes_updated_by_name": r.test_notes_updated_by.shown_name if r.test_notes_updated_by else None,
                "test_cases": case_view_map.get(r.id, []),
                "free_bugs": free_bug_map.get(r.id, []),
                "auto_linked_case_count": sum(1 for c in case_view_map.get(r.id, []) if c.get("auto_linked")),
                "auto_linked_bug_count": len(auto_story_bug_map.get(r.id, [])),
            }
            for r in reqs
        ]

    def get_retest_workbench(
        self,
        *,
        current_user: User,
        major_version_id: int | None = None,
        mode: str = "version",
        software_id: int | None = None,
    ) -> list[dict]:
        query = (
            self.db.query(Requirement)
            .options(
                joinedload(Requirement.owner),
                joinedload(Requirement.test_cases),
                joinedload(Requirement.retester),
                joinedload(Requirement.major_version),
            )
            .filter(
                Requirement.test_completed.is_(True),
                Requirement.owner_id.isnot(None),
                Requirement.owner_id != current_user.id,
            )
        )

        if mode == "version":
            if not major_version_id:
                return []
            query = query.filter(Requirement.major_version_id == major_version_id)
        elif mode == "all_pending":
            query = query.filter(Requirement.retest_completed.is_(False))
            if software_id:
                query = query.join(Version, Requirement.major_version_id == Version.id).filter(Version.software_id == software_id)
        else:
            raise HTTPException(status_code=400, detail="mode only supports version/all_pending")

        reqs = query.order_by(Requirement.id.asc()).all()
        minors = self.link_service.minor_version_name_map()
        case_view_map = self.link_service.build_requirement_case_view(reqs, minors)
        free_bug_map, _ = self.link_service.build_requirement_free_bug_view(reqs, minors, include_retest=False)
        retest_bug_map, _ = self.link_service.build_requirement_free_bug_view(reqs, minors, include_retest=True)
        retest_evidence_map = self.link_service.build_retest_evidence(reqs, minors)

        result = []
        for r in reqs:
            evidence = retest_evidence_map.get(r.id, [])
            existing_retest_ids = {b["id"] for b in retest_bug_map.get(r.id, [])}
            existing_free_ids = {b["id"] for b in free_bug_map.get(r.id, [])}
            existing_case_bug_ids = {
                b["id"]
                for case in case_view_map.get(r.id, [])
                for b in (case.get("bugs") or [])
            }
            # Only surface evidence that isn't already shown elsewhere on the
            # card to avoid duplicate chips.
            filtered_evidence = [
                bug for bug in evidence
                if bug["id"] not in existing_retest_ids
                and bug["id"] not in existing_free_ids
                and bug["id"] not in existing_case_bug_ids
            ]
            result.append({
                "id": r.id,
                "zentao_req_id": r.zentao_req_id,
                "title": r.title,
                "major_version_id": r.major_version_id,
                "major_version_name": r.major_version.version_no if r.major_version else "未知",
                "owner": r.owner.shown_name if r.owner else None,
                "retest_completed": r.retest_completed,
                "retest_passed": r.retest_passed,
                "retest_minor_version_id": r.retest_minor_version_id,
                "retested_by": r.retester.shown_name if r.retester else None,
                "zentao_story_id": r.zentao_story_id,
                "test_completed_at": r.test_completed_at.isoformat() if r.test_completed_at else None,
                "test_cases": case_view_map.get(r.id, []),
                "free_bugs": free_bug_map.get(r.id, []),
                "retest_bugs": retest_bug_map.get(r.id, []),
                "retest_evidence_bugs": filtered_evidence,
                "auto_linked_case_count": sum(1 for c in case_view_map.get(r.id, []) if c.get("auto_linked")),
                "auto_linked_evidence_count": len(filtered_evidence),
            })
        return result
