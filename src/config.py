from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import os
import tomllib

class Settings(BaseSettings):
    database_url: str = Field(default="sqlite:///./job_engine.db")
    dry_run: bool = Field(default=True)
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def load_config(cls, toml_path="config.toml"):
        data = {}
        if os.path.exists(toml_path):
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
        return cls(**data)

settings = Settings.load_config()
