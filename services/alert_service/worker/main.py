"""
Alert Detection Service - Schema-per-Tenant Architecture
"""
import asyncio
import asyncpg
import signal
import logging
import sys
from typing import Dict, List

from config import config
from repository import RuleRepository, AlertRepository
from rules_engine import RulesEngine
from kafka_consumer import AlertKafkaConsumer
from feature_store import FeatureStore

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AlertService:
    """Main Alert Detection Service with schema-per-tenant support"""
    
    def __init__(self):
        self.db_pool = None
        self.feature_store = None
        self.rules_engine = None
        self.kafka_consumer = None
        self.running = False
        self.reload_task = None
        self.consume_task = None
    
    async def initialize(self):
        """Initialize database connection and load rules"""
        logger.info("Initializing Alert Detection Service...")
        
        # Validate configuration
        config.validate()
        
        # Create database connection pool
        logger.info(f"Connecting to database: {config.TIMESCALEDB_HOST}")
        self.db_pool = await asyncpg.create_pool(
            host=config.TIMESCALEDB_HOST,
            port=config.TIMESCALEDB_PORT,
            database=config.TIMESCALEDB_DB,
            user=config.ALERT_SERVICE_DB_USER,
            password=config.ALERT_SERVICE_DB_PASSWORD,
            min_size=5,
            max_size=10,
            command_timeout=60
        )

        # Initialize Feature Store
        logger.info("Initializing Feature Store...")
        redis_url = getattr(config, 'REDIS_URL', 'redis://redis:6379/0')
        self.feature_store = FeatureStore(
            redis_url=redis_url,
            max_window=100,
            ttl_seconds=3600
        )
        logger.info(f"Feature Store initialized: {redis_url}")

        # Load rules from all tenant schemas
        await self._load_rules()
        
        # Initialize Kafka consumer
        logger.info("Starting Kafka consumer...")
        self.kafka_consumer = AlertKafkaConsumer(
            config=config,
            rules_engine=self.rules_engine
        )
        await self.kafka_consumer.start()
        
        logger.info("Alert Detection Service initialized successfully")
    
    async def _load_rules(self):
        """Load alert rules from all tenant schemas"""
        rule_repo = RuleRepository(self.db_pool)
        alert_repo = AlertRepository(self.db_pool)
        
        # Load rules from all tenants
        tenant_rules = await rule_repo.load_all_tenant_rules()
        
        total_rules = sum(len(rules) for rules in tenant_rules.values())
        logger.info(f"Loaded {total_rules} rules from {len(tenant_rules)} tenants")
        
        # Initialize or update rules engine
        if self.rules_engine:
            self.rules_engine.update_rules(tenant_rules)
        else:
            self.rules_engine = RulesEngine(
                tenant_rules=tenant_rules,
                alert_repo=alert_repo,
                feature_store=self.feature_store
            )
    
    async def _periodic_reload(self):
        """Periodically reload rules from database"""
        while self.running:
            try:
                await asyncio.sleep(config.RULE_RELOAD_INTERVAL)
                logger.info("Reloading alert rules from database...")
                await self._load_rules()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error during rule reload: {e}", exc_info=True)
    
    async def run(self):
        """Main service loop"""
        self.running = True
        
        # Start periodic rule reload task
        self.reload_task = asyncio.create_task(self._periodic_reload())
        
        # Start Kafka consumer task
        self.consume_task = asyncio.create_task(
            self.kafka_consumer.consume_messages()
        )
        
        logger.info("Alert Detection Service is running")
        logger.info(f"Rule reload interval: {config.RULE_RELOAD_INTERVAL}s")
        
        # Wait for tasks
        await asyncio.gather(
            self.reload_task,
            self.consume_task,
            return_exceptions=True
        )
    
    async def shutdown(self):
        """Gracefully shutdown the service"""
        logger.info("Shutting down Alert Detection Service...")
        self.running = False
        
        # Cancel tasks
        if self.reload_task:
            self.reload_task.cancel()
        if self.consume_task:
            self.consume_task.cancel()
        
        # Stop Kafka consumer
        if self.kafka_consumer:
            await self.kafka_consumer.stop()

        # Close Feature Store
        if self.feature_store:
            await self.feature_store.close()
            logger.info("Feature Store closed")

        # Close database pool
        if self.db_pool:
            await self.db_pool.close()

        logger.info("Alert Detection Service stopped")


# Global service instance
service = None


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}")
    if service:
        asyncio.create_task(service.shutdown())


async def main():
    """Main entry point"""
    global service
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Create and initialize service
        service = AlertService()
        await service.initialize()
        
        # Run service
        await service.run()
    
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if service:
            await service.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
