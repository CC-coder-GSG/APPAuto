from pydantic import BaseModel


class BugOut(BaseModel):
    id: int
    bug_id: str
