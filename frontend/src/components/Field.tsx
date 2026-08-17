import type { InputHTMLAttributes } from "react";
import "./Field.css";

interface Props extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
}

export function Field({ label, hint, id, ...rest }: Props) {
  const inputId = id ?? `f-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <div className="field">
      {/* Nhãn luôn hiện, không dùng placeholder thay nhãn: placeholder biến mất
          ngay khi gõ chữ đầu tiên và người lớn tuổi quên mất ô này là ô gì. */}
      <label className="field__label" htmlFor={inputId}>
        {label}
      </label>
      <input className="field__input" id={inputId} {...rest} />
      {hint && <p className="field__hint">{hint}</p>}
    </div>
  );
}
