from sqlalchemy import create_engine
from .sql import SQLRepository


class PostgresRepository(SQLRepository):
    def __init__(self, url):
        super().__init__(create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5}), "postgresql")
