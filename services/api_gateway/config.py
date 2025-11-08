from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # API Configuration
    API_TITLE: str = "IndustryFlow API"
    API_VERSION: str = "1.0.0"
    API_DESCRIPTION: str = "Real-time IoT Data Processing Platform"
    
    # Database Configuration
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str
    API_GATEWAY_DB_USER: str
    API_GATEWAY_DB_PASSWORD: str
    
    # Redis Configuration
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int
    
    # Kafka Configuration
    KAFKA_BOOTSTRAP_SERVERS: str
    KAFKA_TOPIC_SENSOR_DATA: str
    
    # JWT Configuration
    jwt_secret_key: str
    
    # CORS Configuration
    CORS_ORIGINS: list = ["http://localhost:3000", "http://localhost:8000"]
    
    @property
    def database_url(self) -> str:
        """Construct async PostgreSQL connection URL"""
        return f"postgresql+asyncpg://{self.API_GATEWAY_DB_USER}:{self.API_GATEWAY_DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
    
    @property
    def redis_url(self) -> str:
        """Construct Redis connection URL"""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    @property
    def kafka_bootstrap_servers(self) -> str:
        """Kafka bootstrap servers"""
        return self.KAFKA_BOOTSTRAP_SERVERS
    
    # Properties for asyncpg pool
    @property
    def timescaledb_host(self) -> str:
        return self.DB_HOST
    
    @property
    def timescaledb_port(self) -> int:
        return self.DB_PORT
    
    @property
    def timescaledb_user(self) -> str:
        return self.API_GATEWAY_DB_USER
    
    @property
    def timescaledb_password(self) -> str:
        return self.API_GATEWAY_DB_PASSWORD
    
    @property
    def timescaledb_database(self) -> str:
        return self.DB_NAME
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

@lru_cache()
def get_settings() -> Settings:
    """Cache settings instance"""
    return Settings()
