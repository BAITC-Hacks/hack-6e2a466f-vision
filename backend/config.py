from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    ekt_api_base_url: str = "https://ekt.kz/api"
    ekt_api_username: str = ""
    ekt_api_password: str = ""
    ekt_api_timeout_seconds: float = 10
    openai_api_key: str = ""
    openai_model: str = "gpt-6-astra"
    openai_timeout_seconds: float = 30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
