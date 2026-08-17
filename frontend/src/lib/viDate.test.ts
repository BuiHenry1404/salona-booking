import { describe, expect, it } from "vitest";
import { formatViDateTime, formatViTime } from "./viDate";

/* Giờ Việt Nam là UTC+7 và không có DST, nên tính ngược từ UTC là an toàn. */

describe("formatViDateTime", () => {
  it("3 giờ chiều thứ Sáu 7/8/2026", () => {
    expect(formatViDateTime("2026-08-07T08:00:00Z")).toBe("Thứ Sáu, 7/8 — 3:00 chiều");
  });

  it("buổi sáng gọi là sáng", () => {
    expect(formatViDateTime("2026-08-07T02:30:00Z")).toBe("Thứ Sáu, 7/8 — 9:30 sáng");
  });

  it("sau 6 giờ chiều gọi là tối", () => {
    expect(formatViDateTime("2026-08-07T12:00:00Z")).toBe("Thứ Sáu, 7/8 — 7:00 tối");
  });

  it("12 giờ trưa KHÔNG được thành 0 giờ", () => {
    expect(formatViDateTime("2026-08-07T05:00:00Z")).toContain("12:00 chiều");
  });

  it("Chủ Nhật không gọi là Thứ Tám", () => {
    expect(formatViDateTime("2026-08-09T08:00:00Z")).toContain("Chủ Nhật");
  });

  it("formatViTime chỉ trả phần giờ", () => {
    expect(formatViTime("2026-08-07T08:00:00Z")).toBe("3:00 chiều");
  });
});
