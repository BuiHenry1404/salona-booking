import { session } from "../auth/session";
import type { Role } from "../auth/session";

const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const FALLBACK: Record<number, string> = {
  401: "Phiên đăng nhập đã hết hạn, cô chú đăng nhập lại giúp con ạ.",
  403: "Phần này chỉ chủ tiệm mới xem được ạ.",
  404: "Không tìm thấy ạ.",
  409: "Giờ này vừa có người đặt mất rồi ạ.",
  429: "Cô chú thử lại sau ít phút giúp con ạ.",
};

/** Refresh token đi bằng cookie HttpOnly scoped /api/v1/auth — request tới
 * nhóm đường dẫn này phải xin cookie theo (cross-origin mặc định là không gửi). */
function isAuthPath(path: string): boolean {
  return path.startsWith("/api/v1/auth");
}

/** 401 của các đường này không có nghĩa "token hết hạn": login sai là sai,
 * refresh chết là hết phiên thật — tự refresh lại là tạo vòng lặp. */
const NO_AUTO_REFRESH = [
  "/api/v1/auth/login",
  "/api/v1/auth/refresh",
  "/api/v1/auth/logout",
];

interface TokenResponse {
  access_token: string;
  token_type: string;
  role: Role;
}

/** Contract của GET /api/v1/appointments/mine và POST /api/v1/appointments
 * (backend: AppointmentResponse trong app/api/v1/schemas.py). */
export interface AppointmentResponse {
  id: string;
  start_at: string; // ISO datetime
  duration_minutes: number;
  note: string | null;
  status: "booked" | "cancelled";
  user_name: string | null;
  phone: string | null;
}

async function parseError(response: Response): Promise<ApiError> {
  let detail: string | undefined;
  try {
    const data = await response.json();
    detail = typeof data?.detail === "string" ? data.detail : undefined;
  } catch {
    /* body rỗng hoặc không phải JSON */
  }
  return new ApiError(
    response.status,
    detail ?? FALLBACK[response.status] ?? "Có lỗi xảy ra, cô chú thử lại giúp con ạ.",
  );
}

/** Một lượt fetch: gắn Bearer từ session HIỆN TẠI (lần retry phải thấy token mới). */
async function rawRequest<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const { token } = session.load();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: isAuthPath(path) ? "include" : undefined,
    });
  } catch {
    // TypeError từ fetch nghĩa là không tới được server. Không bao giờ hiện
    // "Failed to fetch" cho khách.
    throw new ApiError(0, "Máy không vào được mạng, cô chú kiểm tra wifi giúp con ạ.");
  }

  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return null as T;
  try {
    return (await response.json()) as T;
  } catch {
    return null as T;
  }
}

/** Đổi cookie HttpOnly lấy access token mới, lưu vào memory. Thành công/false,
 * không ném — lỗi ở đây là "hết phiên", không phải exception cho caller. */
async function refreshAccessToken(): Promise<boolean> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/api/v1/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
  } catch {
    return false;
  }
  if (!response.ok) return false;
  try {
    const data = (await response.json()) as TokenResponse;
    session.save(data.access_token, data.role);
    return true;
  } catch {
    return false;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  try {
    return await rawRequest<T>(method, path, body);
  } catch (err) {
    const expired =
      err instanceof ApiError &&
      err.status === 401 &&
      !NO_AUTO_REFRESH.some((p) => path === p || path.startsWith(`${p}/`));

    if (!expired) throw err;

    // Đúng MỘT lượt refresh, đúng MỘT lượt retry — không đệ quy, không vòng lặp.
    if (!(await refreshAccessToken())) {
      session.clear();
      throw err;
    }
    try {
      return await rawRequest<T>(method, path, body);
    } catch (retryErr) {
      if (retryErr instanceof ApiError && retryErr.status === 401) session.clear();
      throw retryErr;
    }
  }
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  /** POST không body (vd /shop/free). KHÔNG dùng `post(path)` — nó tự nhét
   * `{}` làm body. Truyền `undefined` xuyên qua request/rawRequest: không
   * body, không Content-Type, và vẫn kế thừa toàn bộ auth/refresh/error. */
  postNoBody: <T>(path: string) => request<T>("POST", path),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};
