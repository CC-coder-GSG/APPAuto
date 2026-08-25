from pydantic import BaseModel


class VersionOut(BaseModel):
    id: int
    version_no: str
