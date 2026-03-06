from pydantic import BaseModel


class RequirementOut(BaseModel):
    id: int
    zentao_req_id: str
