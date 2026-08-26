import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import "./Button.css";

type Variant = "primary" | "accent" | "danger" | "ghost";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  fullWidth?: boolean;
  loading?: boolean;
  children: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = "primary", fullWidth = true, loading = false, disabled, children, className, ...rest },
  ref,
) {
  const classes = ["btn", `btn--${variant}`, fullWidth ? "btn--full" : null, className]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      ref={ref}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {/* Trạng thái chạy phải nói bằng chữ: người lớn tuổi không đọc được
          spinner, và mù màu nhẹ thì đổi màu cũng vô nghĩa. */}
      {loading ? "Đang xử lý…" : children}
    </button>
  );
});
