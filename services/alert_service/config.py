"""
Configuration for Alert Service Worker
Schema-per-tenant architecture
"""
import os
import sys
import logging

logger = logging.getLogger(__name__)


class AlertServiceConfig:
    """Alert service configuration for schema-per-tenant"""
    
    # Database Configuration (single user, schema routing)
    TIMESCALEDB_HOST = os.getenv('TIMESCALEDB_HOST', 'timescaledb')
    TIMESCALEDB_PORT = int(os.getenv('TIMESCALEDB_PORT', '5432'))
    TIMESCALEDB_DB = os.getenv('TIMESCALEDB_DB', 'industryflow')
    ALERT_SERVICE_DB_USER = os.getenv('ALERT_SERVICE_DB_USER')
    ALERT_SERVICE_DB_PASSWORD = os.getenv('ALERT_SERVICE_DB_PASSWORD')
    
    # Kafka Configuration
    KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS')
    KAFKA_TOPIC_SENSOR_DATA = os.getenv('KAFKA_TOPIC_SENSOR_DATA')
    KAFKA_TOPIC_ALERTS = os.getenv('KAFKA_TOPIC_ALERTS')
    KAFKA_GROUP_ID = os.getenv('KAFKA_GROUP_ID')
    
    # Service Settings
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    RULE_RELOAD_INTERVAL = int(os.getenv('RULE_RELOAD_INTERVAL', '60'))
    
    @property
    def database_url(self):
        """Get database connection URL"""
        return (
            f"postgresql://{self.ALERT_SERVICE_DB_USER}:{self.ALERT_SERVICE_DB_PASSWORD}"
            f"@{self.TIMESCALEDB_HOST}:{self.TIMESCALEDB_PORT}/{self.TIMESCALEDB_DB}"
        )
    
    def validate(self):
        """Validate required configuration on startup"""
        required = {
            'ALERT_SERVICE_DB_USER': self.ALERT_SERVICE_DB_USER,
            'ALERT_SERVICE_DB_PASSWORD': self.ALERT_SERVICE_DB_PASSWORD,
            'KAFKA_BOOTSTRAP_SERVERS': self.KAFKA_BOOTSTRAP_SERVERS,
            'KAFKA_TOPIC_SENSOR_DATA': self.KAFKA_TOPIC_SENSOR_DATA,
            'KAFKA_TOPIC_ALERTS': self.KAFKA_TOPIC_ALERTS,
            'KAFKA_GROUP_ID': self.KAFKA_GROUP_ID
        }
        
        missing = [key for key, value in required.items() if not value]
        
        if missing:
            logger.error(f"ERROR: Missing required environment variables: {', '.join(missing)}")
            sys.exit(1)
        
        logger.info(f"Configuration validated: {self.ALERT_SERVICE_DB_USER}@{self.TIMESCALEDB_HOST}")


config = AlertServiceConfig()
