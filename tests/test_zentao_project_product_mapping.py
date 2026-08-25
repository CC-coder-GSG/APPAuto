from __future__ import annotations

from app.services.zentao_version_sync_service import ZentaoVersionSyncService


class _ExecutionDetailClient:
    def get(self, path, params=None):
        assert path == "executions/1639"
        return {
            "id": 1639,
            "project": 1233,
            "products": [{"id": 311, "name": "Infinity Studio"}],
        }


def test_project_product_id_is_resolved_from_execution_detail():
    products = ZentaoVersionSyncService._resolve_products_from_executions(
        _ExecutionDetailClient(),
        [{"id": 1639, "name": "V0.0.4", "project": 1233}],
    )
    assert products == [{"id": 311, "name": "Infinity Studio"}]
