from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
import structlog

from app.api.deps import get_db
from app.api.v1.schemas import HealthResponse
from app.core.clock import now_utc

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/", response_model=HealthResponse, summary="Health check")
async def health_check():
    """Chỉ báo tiến trình còn sống, KHÔNG chạm DB — đây là liveness.

    Đừng thêm kiểm tra Mongo vào đây: liveness fail thì orchestrator giết và
    khởi động lại container, mà restart không làm Mongo sống lại. Việc đó là
    của /ready.
    """
    return HealthResponse(status="healthy", timestamp=now_utc())


@router.get("/ready", response_model=HealthResponse, summary="Readiness check")
async def readiness_check(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Readiness check that verifies database connectivity.

    Mongo chết phải trả 503, không phải 200 kèm chữ "not ready" trong body:
    k8s probe, docker HEALTHCHECK và load balancer đều chỉ nhìn status code.
    Trả 200 nghĩa là instance hỏng vẫn được nhận traffic — đúng kịch bản mongod
    tắt SAU khi app đã khởi động, lúc mà kiểm tra ở startup không cứu được nữa.
    """
    try:
        await db.command("ping")
    except Exception as exc:
        logger.warning("readiness_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không kết nối được cơ sở dữ liệu",
        )

    return HealthResponse(status="ready", timestamp=now_utc())
