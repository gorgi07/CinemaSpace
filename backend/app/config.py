from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = 'postgresql+asyncpg://cinema:cinema_dev_password@localhost:5432/cinema'
    redis_primary_url: str = 'redis://localhost:6379/0'
    redis_replica_url: str = 'redis://localhost:6380/0'
    redis_replica_host: str = 'redis-primary'
    secret_key: str = 'change-this-long-random-key-in-production'
    admin_email: str = 'admin@cinema.local'
    admin_password: str = 'AdminDemo123!'
    smtp_host: str = 'localhost'
    smtp_port: int = 1025
    mail_from: str = 'cinema@local.test'
    cors_origins: str = 'http://localhost:3000'
    hold_seconds: int = 180
    lock_ms: int = 8000
    categories_ttl_seconds: int = 90
    firestore_project_id: str = 'cinema-lab-local'
    firestore_mode: str = 'emulator'
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

settings = Settings()
