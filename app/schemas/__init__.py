"""Schema exports."""

from app.schemas.admin import JenkinsBuildReportPayload
from app.schemas.zentao_sync import ZentaoBrowserSyncPayload

__all__ = ["JenkinsBuildReportPayload", "ZentaoBrowserSyncPayload"]
