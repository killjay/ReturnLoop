from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.db.database import init_db
from backend.api.returns import router as returns_router
from backend.api.dashboard import router as dashboard_router
from backend.api.webhooks import router as webhooks_router
from backend.api.ws import router as ws_router
from backend.api.agents_api import router as agents_router
from backend.api.airbyte import router as airbyte_router
from backend.api.shopify_webhooks import router as shopify_router
from backend.api.shopify_oauth import router as shopify_oauth_router
from backend.orchestrator.event_bus import event_bus
from backend.orchestrator.pipeline import init_pipeline
from backend.services.aerospike_client import aerospike_client

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    aerospike_client.connect()
    event_bus.start()
    await init_pipeline()
    yield
    # Shutdown
    event_bus.stop()


app = FastAPI(
    title="Return Loop",
    description="AI-powered multi-agent ecommerce returns optimization",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(returns_router, prefix="/api/returns", tags=["returns"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(webhooks_router, prefix="/api/webhooks", tags=["webhooks"])
app.include_router(ws_router, prefix="/ws", tags=["websocket"])
app.include_router(agents_router, prefix="/api/agents", tags=["agents"])
app.include_router(airbyte_router, prefix="/api/airbyte", tags=["airbyte"])
app.include_router(shopify_router, prefix="/api/webhooks/shopify", tags=["shopify"])
app.include_router(shopify_oauth_router, prefix="/shopify", tags=["shopify-oauth"])


@app.get("/")
async def root():
    return {
        "name": "Return Loop",
        "version": "1.0.0",
        "description": "Closing the loop on ecommerce returns",
        "agents": ["prophet", "whisperer", "loop_matcher", "recoverer", "learner"],
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}
