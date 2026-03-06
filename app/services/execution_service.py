from sqlalchemy.orm import Session


class ExecutionService:
    def __init__(self, db: Session):
        self.db = db
