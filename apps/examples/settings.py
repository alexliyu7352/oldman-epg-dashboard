"""Deployment-owned upstream address for the backend HTTP example."""

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, field_validator


class ExamplesSettings(BaseModel):
    """Use public httpbin by default, or a deployment's compatible instance."""

    model_config = ConfigDict(extra="forbid")

    http_base_url: AnyHttpUrl = AnyHttpUrl("https://httpbin.org")

    @field_validator("http_base_url")
    @classmethod
    def validate_base_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """Fixed operation paths must not be appended to credentials or a query."""
        if value.username or value.password or value.query is not None or value.fragment is not None:
            raise ValueError("HTTP example base URL cannot include credentials, a query or a fragment")
        return value
