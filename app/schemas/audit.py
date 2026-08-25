from pydantic import BaseModel


class AuditLogOut(BaseModel):
    id: int
    action: str
