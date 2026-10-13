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
    KAFKA_GROUP_ID = os.getenv('KAFKA_GROUP_ID')

    # At-least-once handling (ADR-0005 dec 3). A reading that fails for an unknown reason is retried
    # KAFKA_MAX_RETRIES times, then dead-lettered; a malformed one is dead-lettered at once; an
    # infrastructure failure is never dead-lettered, the batch is rewound and retried with a backoff
    # that doubles from KAFKA_RETRY_BACKOFF_SECONDS up to KAFKA_MAX_BACKOFF_SECONDS.
    KAFKA_MAX_RETRIES = int(os.getenv('KAFKA_MAX_RETRIES', '3'))
    KAFKA_RETRY_BACKOFF_SECONDS = float(os.getenv('KAFKA_RETRY_BACKOFF_SECONDS', '1.0'))
    KAFKA_MAX_BACKOFF_SECONDS = float(os.getenv('KAFKA_MAX_BACKOFF_SECONDS', '30'))
    KAFKA_TOPIC_DLQ = os.getenv('KAFKA_TOPIC_DLQ') or (
        f"{KAFKA_TOPIC_SENSOR_DATA}.dlq" if KAFKA_TOPIC_SENSOR_DATA else None
    )

    # Latest-value snapshot store the ML rules read (ADR-0023 dec 7). Required: the worker refuses
    # to start without it rather than silently running ML rules against nothing.
    REDIS_URL = os.getenv('REDIS_URL')
    # A reading older than this is not part of the current snapshot, so a sensor that stopped
    # reporting cannot be scored as if its last value were current.
    FEATURE_MAX_AGE_SECONDS = float(os.getenv('FEATURE_MAX_AGE_SECONDS', '300'))

    # Liveness: the consumer loop touches this file on every poll; healthcheck.py fails once it is
    # older than HEARTBEAT_MAX_AGE_SECONDS (well above the longest backoff).
    HEARTBEAT_FILE = os.getenv('HEARTBEAT_FILE', '/tmp/alert-worker-heartbeat')
    HEARTBEAT_MAX_AGE_SECONDS = float(os.getenv('HEARTBEAT_MAX_AGE_SECONDS', '120'))

    # Alert dedup (ADR-0005 dec 6): a repeat in the same scope within the cooldown of its event
    # time is suppressed. The cache only spares the database a query, so it is bounded.
    ALERT_COOLDOWN_SECONDS = float(os.getenv('ALERT_COOLDOWN_SECONDS', '300'))
    ALERT_DEDUP_CACHE_MAX = int(os.getenv('ALERT_DEDUP_CACHE_MAX', '10000'))

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
    
    # Variables with no usable default: the worker refuses to start without them, and every
    # deployment (compose, Helm) must set them (tests/test_config_parity.py holds both to this).
    REQUIRED_ENV = (
        'ALERT_SERVICE_DB_USER',
        'ALERT_SERVICE_DB_PASSWORD',
        'KAFKA_BOOTSTRAP_SERVERS',
        'KAFKA_TOPIC_SENSOR_DATA',
        'KAFKA_GROUP_ID',
        'REDIS_URL',
    )

    def validate(self):
        """Validate required configuration on startup"""
        missing = [key for key in self.REQUIRED_ENV if not getattr(self, key)]
        
        if missing:
            logger.error(f"ERROR: Missing required environment variables: {', '.join(missing)}")
            sys.exit(1)
        
        logger.info(f"Configuration validated: {self.ALERT_SERVICE_DB_USER}@{self.TIMESCALEDB_HOST}")


config = AlertServiceConfig()
