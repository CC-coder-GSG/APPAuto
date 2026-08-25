from pydantic import BaseModel


class ReportOverview(BaseModel):
    executed_requirements: int = 0
