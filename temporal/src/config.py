from __future__ import annotations
from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    temporal_address: str = Field("temporal:7233", env="TEMPORAL_ADDRESS")
    temporal_namespace: str = Field("default", env="TEMPORAL_NAMESPACE")
    temporal_task_queue: str = Field("main", env="TEMPORAL_TASK_QUEUE")
    supabase_url: str = Field("http://host.docker.internal:54321", env="SUPABASE_URL")
    supabase_service_role_key: str = Field("dev-service-role-key", env="SUPABASE_SERVICE_ROLE_KEY")

    smtp_host: str = 'inbucket'
    smtp_port: int = 1025
    smtp_from: str = 'template@localhost'
    smtp_starttls: bool = False
    smtp_username: str = ''
    smtp_password: str = ''

    class Config:
        case_sensitive = False

settings = Settings()
