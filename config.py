from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    # LLM（OpenAI 兼容；mock 模式完全离线）
    api_key: str = ""
    model_name: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    LLM_MODE: str = "auto"  # auto | real | mock

    # 主数据库（留空 → data/p1.db；填写 postgresql://... 切换 PostgreSQL）
    P1_DATABASE_URL: str = ""

    # 语音识别（DashScope Qwen-ASR-Realtime；无 Key 时自动 mock）
    ASR_MODE: str = "auto"  # auto | real | mock
    ASR_PROVIDER: str = "qwen3-asr-flash-realtime"
    ASR_API_KEY: str = ""
    ASR_BASE_URL: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    ASR_MODEL_NAME: str = "qwen3-asr-flash-realtime"
    ASR_LANGUAGE: str = "zh"
    ASR_SAMPLE_RATE: int = 16000
    ASR_AUDIO_FORMAT: str = "pcm"
    ASR_TIMEOUT_SECONDS: float = 30.0
    ASR_MOCK_TEXT: str = "上周八年级各班出勤率"

    # LLM 观测（Langfuse；默认关闭，保证离线确定性）
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    LANGFUSE_ENVIRONMENT: str = "production"
    LANGFUSE_RELEASE: str = "smartdeck-0.2.0"
    LANGFUSE_TIMEOUT_SECONDS: float = 5.0
    LANGFUSE_FLUSH_INTERVAL_SECONDS: float = 1.0
    LANGFUSE_CAPTURE_CONTENT: bool = True

    # 效果评测（只写内存与本地 JSONL/JSON，不新增数据库表）
    EVAL_FAILURE_LOG: bool = False
    EVAL_FAILURE_LOG_PATH: str = "data/evals/failure_cases.jsonl"
    EVAL_GOLDEN_OUTPUT: str = "data/evals/latest.json"
    TEXT2SQL_SCHEMA_RETRIEVAL: bool = True

    # 飞书群机器人（无应用凭据时自动 mock）
    FEISHU_MODE: str = "auto"  # auto | real | mock
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_VERIFICATION_TOKEN: str = ""
    FEISHU_ENCRYPT_KEY: str = ""
    FEISHU_API_BASE: str = "https://open.feishu.cn"
    FEISHU_DEFAULT_CHAT_ID: str = ""
    FEISHU_MOCK_CHAT_ID: str = "oc_mock_campus"
    FEISHU_ALLOW_WRITES: bool = False
    FEISHU_LONG_CONNECTION: bool = True
    FEISHU_TIMEOUT_SECONDS: float = 15.0

    @field_validator("base_url", "ASR_BASE_URL", "FEISHU_API_BASE", "LANGFUSE_HOST")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        scheme, separator, rest = value.partition("://")
        if separator:
            return f"{scheme.lower()}://{rest}"
        return value

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
