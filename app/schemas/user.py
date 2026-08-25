from pydantic import BaseModel, Field


class PasswordChangeSelf(BaseModel):
    old_password: str
    new_password: str = Field(min_length=3, max_length=128)
