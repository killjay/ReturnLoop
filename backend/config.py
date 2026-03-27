from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite+aiosqlite:///./returnloop.db"

    # Anthropic Claude API
    anthropic_api_key: str = ""
    claude_model_id: str = "claude-sonnet-4-20250514"

    # Bland AI
    bland_ai_api_key: str = ""
    bland_ai_webhook_url: str = "http://localhost:8000/api/webhooks/bland-ai"
    bland_ai_pathway_id: str = ""

    # Aerospike
    aerospike_host: str = "localhost"
    aerospike_port: int = 3000
    aerospike_namespace: str = "returnloop"

    # Shopify (static custom app)
    shopify_store_url: str = ""
    shopify_api_token: str = ""
    shopify_api_secret: str = ""

    # Shopify OAuth (Return Stuff Partner app)
    shopify_oauth_client_id: str = ""
    shopify_oauth_client_secret: str = ""

    # Auth0
    auth0_domain: str = ""
    auth0_client_id: str = ""
    auth0_client_secret: str = ""
    auth0_audience: str = ""

    # Overmind
    overmind_api_key: str = ""
    overmind_project_id: str = ""

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    frontend_url: str = "http://localhost:5173"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
