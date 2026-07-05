# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Configuration for Alert Service Worker
Schema-per-tenant architecture
"""
import os
import sys
import ssl
import logging

logger = logging.getLogger(__name__)


def db_ssl_context():
    """SSLContext for DB connections per DB_SSLMODE (ADR-0017). Default verify-full: encrypt +
    verify the server cert against DB_SSLROOTCERT (the internal CA) with hostname checking."""
    mode = (os.getenv("DB_SSLMODE") or "verify-full").lower()
    if mode in ("disable", "allow", ""):
        return None
    ca = os.getenv("DB_SSLROOTCERT") or None
    if ca and not os.path.exists(ca):
        ca = None
    ctx = ssl.create_default_context(cafile=ca)
    if mode in ("require", "prefer"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif mode == "verify-ca":
        ctx.check_hostname = False
    return ctx


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

    # ML service (service-to-service). Used by the real-time ml rules (inference) and the
    # scheduled drift evaluator (ADR-0021). INTERNAL_SERVICE_TOKEN authenticates the call so
    # ml-service trusts the company_id in the body; unset => the internal path fails closed.
    ML_SERVICE_URL = os.getenv('ML_SERVICE_URL', 'http://ml-service-api:8002')
    INTERNAL_SERVICE_TOKEN = os.getenv('INTERNAL_SERVICE_TOKEN')

    # Model-drift evaluation (ADR-0021 decision 5): windowed + periodic, independent of the
    # per-reading anomaly path. Cadence and window are configuration, not architecture.
    DRIFT_EVAL_INTERVAL = int(os.getenv('DRIFT_EVAL_INTERVAL', '3600'))   # hourly
    DRIFT_WINDOW_MINUTES = int(os.getenv('DRIFT_WINDOW_MINUTES', '1440'))  # trailing 24h
    DRIFT_SHARE_THRESHOLD = float(os.getenv('DRIFT_SHARE_THRESHOLD', '0.5'))

    # Retrain recommendation (ADR-0022 dec 4): the closed loop is a *recommendation*, not
    # automated training. Raised only when a model shows BOTH sustained drift AND label-derived
    # precision decay — so a drift blip alone or a couple of bad labels alone won't trigger it.
    # Evaluated on the same schedule as drift; all thresholds are configuration.
    RETRAIN_RECO_ENABLED = os.getenv('RETRAIN_RECO_ENABLED', 'true').lower() == 'true'
    RETRAIN_PRECISION_FLOOR = float(os.getenv('RETRAIN_PRECISION_FLOOR', '0.6'))    # below this = decayed
    RETRAIN_MIN_LABELS = int(os.getenv('RETRAIN_MIN_LABELS', '10'))                 # min decided labels to trust precision
    RETRAIN_MIN_DRIFT_ALERTS = int(os.getenv('RETRAIN_MIN_DRIFT_ALERTS', '2'))      # "sustained" drift
    RETRAIN_PRECISION_WINDOW_DAYS = int(os.getenv('RETRAIN_PRECISION_WINDOW_DAYS', '30'))
    RETRAIN_DRIFT_LOOKBACK_HOURS = int(os.getenv('RETRAIN_DRIFT_LOOKBACK_HOURS', '72'))
    RETRAIN_RECO_COOLDOWN_SECONDS = float(os.getenv('RETRAIN_RECO_COOLDOWN_SECONDS', '86400'))  # once/day/model
    RETRAIN_RECO_SEVERITY = os.getenv('RETRAIN_RECO_SEVERITY', 'high')
    
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
