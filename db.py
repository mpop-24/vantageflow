import os
from sqlmodel import SQLModel, create_engine


def get_engine():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return create_engine(database_url, echo=False)


def init_db(engine):
    SQLModel.metadata.create_all(engine)
