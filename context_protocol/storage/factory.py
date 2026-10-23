from sqlalchemy.engine import make_url


def normalize_url(value):
    url = make_url(value.removeprefix("jdbc:"))
    drivers = {"postgresql": "postgresql+psycopg", "postgres": "postgresql+psycopg",
               "mysql": "mysql+pymysql", "sqlite": "sqlite+pysqlite"}
    if url.get_backend_name() not in drivers:
        raise ValueError("Unsupported storage backend; choose postgresql, sqlite or mysql")
    return url.set(drivername=drivers[url.get_backend_name()])


def create_repository(value):
    url = normalize_url(value)
    backend = url.get_backend_name()
    if backend == "postgresql":
        from .postgres import PostgresRepository
        return PostgresRepository(url)
    if backend == "sqlite":
        from .sqlite import SQLiteRepository
        return SQLiteRepository(url)
    from .mysql import MySQLRepository
    return MySQLRepository(url)
