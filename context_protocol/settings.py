"""Only project-specific variables configure runtime persistence."""
import os
from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)
    database_url: str

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.getenv("CONTEXT_DATABASE_URL") or "postgresql+psycopg://context:context_local@127.0.0.1:5433/context_protocol",
        )
