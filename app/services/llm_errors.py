from typing import Any


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
        return {
            "message": self.message,
            "category": self.category,
            "status_code": self.status_code,
            "model": self.model,
            "api_base_url": self.api_base_url,
        }
