"""Overmind integration for continuous agent optimization."""
import httpx
from backend.config import get_settings

settings = get_settings()


class OvermindClient:
    """Logs agent decisions to Overmind for continuous optimization.

    Falls back to local logging if Overmind is not configured.
    """

    def __init__(self):
        self.api_key = settings.overmind_api_key
        self.project_id = settings.overmind_project_id
        self._local_log = []

    async def log_decision(self, agent_name: str, decision_data: dict):
        """Log an agent decision for optimization."""
        entry = {
            "agent": agent_name,
            "data": decision_data,
        }

        if self.api_key:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"https://api.overmind.ai/v1/projects/{self.project_id}/decisions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=entry,
                        timeout=5.0,
                    )
            except Exception as e:
                print(f"Overmind log error: {e}")
                self._local_log.append(entry)
        else:
            self._local_log.append(entry)

    async def get_optimization_hints(self, agent_name: str) -> dict:
        """Get optimization hints from Overmind for an agent."""
        if self.api_key:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"https://api.overmind.ai/v1/projects/{self.project_id}/hints/{agent_name}",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        timeout=5.0,
                    )
                    return response.json()
            except Exception:
                pass
        return {"hints": [], "message": "Using default strategies"}

    def get_local_log(self) -> list:
        return self._local_log


# Singleton
overmind_client = OvermindClient()
