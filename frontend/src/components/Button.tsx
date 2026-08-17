import type { ButtonHTMLAttributes, ReactNode } from "react";
import "./Button.css";

type Variant = "primary" | "accent" | "danger" | "ghost";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  fullWidth?: boolean;
  loading?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  fullWidth = true,
  loading = false,
  disabled,
  children,
  ...rest
}: Props) {
  return (
    <button
      className={`btn btn--${variant}${fullWidth ? " btn--full" : ""}`}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {/* Trạng thái chạy phải nói bằng chữ: người lớn tuổi không đọc được
          spinner, và mù màu nhẹ thì đổi màu cũng vô nghĩa. */}
      {loading ? "Đang xử lý…" : children}
    </button>
  );
}
