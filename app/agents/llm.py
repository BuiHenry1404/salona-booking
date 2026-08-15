from typing import List, Optional

from langchain_openai import AzureChatOpenAI

from app.core.config import settings


def build_chat_model(
    tags: Optional[List[str]] = None,
    temperature: float = 0.0,
    streaming: bool = True,
) -> AzureChatOpenAI:
    """Model cho một node cụ thể.

    `tags` là thứ cho phép lọc sự kiện stream: chỉ node respond được gắn
    tag "respond", và chỉ token mang tag đó mới được đẩy ra màn hình khách.
    Không có tags thì token JSON định tuyến của supervisor sẽ chạy ngang màn hình.

    `streaming=False` dành cho những lượt gọi sinh JSON chứ không sinh câu trả
    lời — parser thời gian ở task 4b là một. Không có gì để hiện dần cho khách xem.
    """
    if not settings.azure_openai_api_key or not settings.azure_openai_endpoint:
        raise RuntimeError("Thiếu AZURE_OPENAI_API_KEY hoặc AZURE_OPENAI_ENDPOINT")

    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        azure_deployment=settings.azure_openai_deployment or settings.azure_openai_model,
        api_version=settings.azure_openai_api_version,
        temperature=temperature,
        tags=tags or [],
        streaming=streaming,
    )
