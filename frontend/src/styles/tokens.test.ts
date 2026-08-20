import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "tokens.css"), "utf8");

function readVar(name: string): string {
  const match = css.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) throw new Error(`Không tìm thấy biến ${name} trong tokens.css`);
  return match[1];
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => {
    const value = parseInt(hex.slice(i, i + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("bảng màu", () => {
  const bg = "#ffffff";

  it.each(["--color-primary", "--color-accent", "--color-danger", "--color-fg", "--color-fg-muted"])(
    "%s đạt tối thiểu 4.5:1 trên nền trắng",
    (name) => {
      expect(contrast(readVar(name), bg)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it("chữ trắng trên nền primary/accent/danger cũng đạt 4.5:1", () => {
    for (const name of ["--color-primary", "--color-accent", "--color-danger"]) {
      expect(contrast("#ffffff", readVar(name))).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("không được lén dùng hai màu đã trượt AA", () => {
    expect(css.toLowerCase()).not.toContain("#0284c7"); // 4.10:1
    expect(css.toLowerCase()).not.toContain("#059669"); // 3.77:1
  });

  it("cỡ chữ nền tối thiểu 19px", () => {
    const size = css.match(/body\s*\{[^}]*font-size:\s*(\d+)px/);
    expect(Number(size?.[1])).toBeGreaterThanOrEqual(19);
  });

  it("nút cao tối thiểu 56px", () => {
    expect(css).toMatch(/--tap:\s*(5[6-9]|[6-9]\d)px/);
  });

  it("có nhánh prefers-reduced-motion", () => {
    expect(css).toContain("prefers-reduced-motion");
  });
});
