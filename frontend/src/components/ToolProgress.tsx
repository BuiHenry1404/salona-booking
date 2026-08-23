import type { ToolStep } from "../hooks/useAgentStream";
import "./ToolProgress.css";

/**
 * Tiến trình gọi tool của AI. Hiện TẤT CẢ các dòng — dòng đã xong GIỮ LẠI
 * trên màn hình để khách thấy máy đã làm gì thay vì nghi máy tự bịa.
 *
 * `step.label` đã là câu tiếng Việt do `toolLabel()` dịch sẵn — không bao giờ
 * render `step.name` (tên kỹ thuật) ra màn hình.
 */
export function ToolProgress({ steps }: { steps: ToolStep[] }) {
  if (steps.length === 0) return null;
  return (
    <ul className="tools" aria-live="polite">
      {steps.map((step, index) => (
        <li
          key={`${step.name}-${index}`}
          className={
            step.done ? (step.ok ? "tool tool--done" : "tool tool--failed") : "tool"
          }
        >
          <span className="tool__mark" aria-hidden="true">
            {step.done ? (step.ok ? "✓" : "✕") : <span className="tool__spin" />}
          </span>
          <span>{step.label}</span>
        </li>
      ))}
    </ul>
  );
}
