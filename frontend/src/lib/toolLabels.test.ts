import { describe, expect, it } from "vitest";
import { TOOL_LABELS, toolLabel } from "./toolLabels";

describe("toolLabel", () => {
  it("dịch đủ sáu tool trong spec", () => {
    expect(Object.keys(TOOL_LABELS).sort()).toEqual([
      "cancel_appointment",
      "find_free_slots",
      "get_shop_status",
      "list_my_appointments",
      "parse_time",
      "propose_appointment",
    ]);
  });

  it("parse_time nói theo việc khách quan tâm, không nói theo việc máy làm", () => {
    // "Đang phân tích thời gian" là câu vô nghĩa với một cụ 70 tuổi.
    expect(toolLabel("parse_time").running).toBe("Đang xem lịch…");
    expect(toolLabel("parse_time").running).not.toMatch(/phân tích|xử lý dữ liệu/i);
  });

  it("dịch find_free_slots sang câu người thường đọc được", () => {
    expect(toolLabel("find_free_slots")).toEqual({
      running: "Đang xem lịch trống…",
      done: "Đã xem lịch trống",
    });
  });

  it("tool lạ thì hiện câu chung, KHÔNG BAO GIỜ hiện tên thô", () => {
    const label = toolLabel("some_new_internal_tool_v2");
    expect(label.running).toBe("Đang xử lý…");
    expect(label.running + label.done).not.toContain("some_new_internal_tool_v2");
    expect(label.running + label.done).not.toMatch(/_/);
  });

  it("không câu nào lọt dấu gạch dưới ra ngoài", () => {
    for (const label of Object.values(TOOL_LABELS)) {
      expect(label.running).not.toMatch(/_/);
      expect(label.done).not.toMatch(/_/);
    }
  });
});
