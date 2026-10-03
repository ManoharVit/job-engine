import pytest
import warnings

# We must pre-import TestClient within a catch_warnings block because
# pytest -Wd overrides filterwarnings in pyproject.toml at the module level.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.base import Base

@pytest.fixture(scope="function")
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
