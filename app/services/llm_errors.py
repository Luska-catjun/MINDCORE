from dataclasses import dataclass
from typing import Any


_SAFE_MESSAGES = {
    "authentication": "Provider authentication failed.",
    "quota_or_rate_limit": "The provider rate limit was reached.",
    "timeout": "The provider request timed out.",
    "network": "The provider is unavailable.",
    "model": "The configured provider model is unavailable.",
    "model_or_api_version": "The configured provider model is unavailable.",
    "configuration": "The provider configuration is invalid.",
    "prompt_configuration": "The Persona prompt configuration is invalid.",
    "request_json": "The provider rejected the request.",
    "response_json": "The provider returned an invalid response.",
    "empty_response": "The provider returned an empty response.",
}


def safe_provider_message(category: str) -> str:
    if category.endswith("_server") or category.endswith("_http"):
        return "The provider is unavailable."
    return _SAFE_MESSAGES.get(category, "The provider request failed.")


@dataclass(frozen=True)
class SafeProviderError:
    """Allow-listed provider metadata suitable for logs and HTTP responses."""

    category: str
    status_code: int | None = None
    model: str | None = None

    def as_http_detail(self) -> dict[str, Any]:
        return {
            "message": safe_provider_message(self.category),
            "category": self.category,
            "status_code": self.status_code,
            "model": self.model,
        }


class LLMError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: str = "unknown",
        status_code: int | None = None,
        model: str | None = None,
        api_base_url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.category = category
        self.status_code = status_code
        self.model = model
        self.api_base_url = api_base_url

    def safe_detail(self) -> dict[str, Any]:
        return SafeProviderError(
            category=self.category,
            status_code=self.status_code,
            model=self.model,
        ).as_http_detail()
