from pydantic import BaseModel


class TestCaseOut(BaseModel):
    id: int
    zentao_case_id: str
