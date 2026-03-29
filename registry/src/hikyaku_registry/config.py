from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    broker_host: str = "0.0.0.0"
    broker_port: int = 8000
    broker_base_url: str = "http://localhost:8000"
    deregistered_task_ttl_days: int = 7
    cleanup_interval_seconds: int = 3600
    auth0_domain: str = ""
    auth0_client_id: str = ""

    model_config = {"env_prefix": ""}


settings = Settings()
