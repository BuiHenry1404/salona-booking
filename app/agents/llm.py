from typing import List, Optional

from langchain_openai import AzureChatOpenAI, ChatOpenAI

from app.core.config import settings

SUPPORTED_CHAT_PROVIDERS = ("azure", "openai", "openai_compatible")


def _missing(names: List[str]) -> str:
    return ", ".join(names)


def build_chat_model(
    tags: Optional[List[str]] = None,
    temperature: float = 0.0,
    streaming: bool = True,
):
    """Model cho một node cụ thể, theo `LLM_PROVIDER` trong .env.

    `tags` là thứ cho phép lọc sự kiện stream: chỉ node respond được gắn
    tag "respond", và chỉ token mang tag đó mới được đẩy ra màn hình khách.
    Không có tags thì token JSON định tuyến của supervisor sẽ chạy ngang màn hình.

    `streaming=False` dành cho những lượt gọi sinh JSON chứ không sinh câu trả
    lời — parser thời gian ở task 4b là một. Không có gì để hiện dần cho khách xem.

    Thiếu cấu hình thì ném lỗi nêu đúng TÊN BIẾN còn thiếu — không bao giờ kèm
    giá trị secret.
    """
    provider = settings.llm_provider

    if provider == "azure":
        missing = [
            name for name, value in (
                ("AZURE_OPENAI_API_KEY", settings.azure_openai_api_key),
                ("AZURE_OPENAI_ENDPOINT", settings.azure_openai_endpoint),
            ) if not value
        ]
        if missing:
            raise RuntimeError(f"Thiếu {_missing(missing)} cho provider azure")

        return AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            azure_deployment=settings.azure_openai_deployment or settings.azure_openai_model,
            api_version=settings.azure_openai_api_version,
            temperature=temperature,
            tags=tags or [],
            streaming=streaming,
        )

    if provider in ("openai_compatible", "openai"):
        if provider == "openai_compatible":
            checks = (
                ("OPENAI_COMPATIBLE_BASE_URL", settings.openai_compatible_base_url),
                ("OPENAI_COMPATIBLE_API_KEY", settings.openai_compatible_api_key),
                ("OPENAI_COMPATIBLE_MODEL", settings.openai_compatible_model),
            )
            base_url = settings.openai_compatible_base_url
            api_key = settings.openai_compatible_api_key
            model = settings.openai_compatible_model
        else:
            checks = (
                ("OPENAI_API_KEY", settings.openai_api_key),
                ("OPENAI_MODEL", settings.openai_model),
            )
            base_url = None
            api_key = settings.openai_api_key
            model = settings.openai_model

        missing = [name for name, value in checks if not value]
        if missing:
            raise RuntimeError(f"Thiếu {_missing(missing)} cho provider {provider}")

        return ChatOpenAI(
            model=model,
            api_key=api_key.get_secret_value(),
            base_url=base_url,
            temperature=temperature,
            tags=tags or [],
            streaming=streaming,
        )

    raise RuntimeError(
        f"Provider '{provider}' chưa được hỗ trợ ở luồng chat. "
        f"Hỗ trợ: {', '.join(SUPPORTED_CHAT_PROVIDERS)}"
    )
