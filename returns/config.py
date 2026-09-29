import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str = "returns.db"
    orders_url: str = "http://localhost:8002"
    redis_url: str = "redis://localhost:6379/0"
    return_window_days: int = 30
    restocking_fee_percent: int = 10
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            database_path=env.get("RETURNS_DATABASE_PATH", cls.database_path),
            orders_url=env.get("ORDERS_URL", cls.orders_url),
            redis_url=env.get("REDIS_URL", cls.redis_url),
            return_window_days=int(env.get("RETURN_WINDOW_DAYS", cls.return_window_days)),
            restocking_fee_percent=int(
                env.get("RESTOCKING_FEE_PERCENT", cls.restocking_fee_percent)
            ),
            log_level=env.get("LOG_LEVEL", cls.log_level),
        )
