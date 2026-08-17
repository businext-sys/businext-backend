import os
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends
from sqlmodel import Session, create_engine

load_dotenv()

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        database_uri = os.getenv("DATABASE_URI")
        if not database_uri:
            raise RuntimeError("DATABASE_URI environment variable is not set")
        _engine = create_engine(
            database_uri,
            echo=False,
            pool_size=5,
            max_overflow=8,
            pool_timeout=30,
            pool_pre_ping=True,
            pool_recycle=300,
        )
    return _engine


def get_session():
    with Session(get_engine()) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
