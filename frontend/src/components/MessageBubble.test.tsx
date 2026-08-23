import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MessageBubble } from "./MessageBubble";

describe("MessageBubble", () => {
  it("user: bong bóng phía người dùng với class riêng", () => {
    render(<MessageBubble role="user" text="mai 3h được không con" />);
    const el = screen.getByText("mai 3h được không con");
    expect(el.className).toMatch(/bubble--me/);
  });

  it("bot: bong bóng phía bot với class riêng", () => {
    render(<MessageBubble role="bot" text="Dạ mai 3 giờ chiều còn trống ạ" />);
    const el = screen.getByText("Dạ mai 3 giờ chiều còn trống ạ");
    expect(el.className).toMatch(/bubble--bot/);
  });

  it("bot: có ảnh đại diện Salona, và ảnh là trang trí (aria-hidden)", () => {
    render(<MessageBubble role="bot" text="Dạ em nghe ạ" />);
    const img = document.querySelector<HTMLImageElement>(".bubble__wrap--bot .avatar");
    expect(img).not.toBeNull();
    expect(img!.getAttribute("src")).toBe("/brand/salona-avatar.png");
    // alt rỗng + aria-hidden: vai người nói đã nằm ở chữ, đọc thêm chỉ ồn.
    expect(img!.getAttribute("alt")).toBe("");
    expect(img!.getAttribute("aria-hidden")).toBe("true");
  });

  it("user: KHÔNG có ảnh đại diện", () => {
    render(<MessageBubble role="user" text="mai 3h nha con" />);
    expect(document.querySelector(".avatar")).toBeNull();
  });

  it("pending: caption 'Đang gửi lại…' đi kèm bong bóng", () => {
    render(<MessageBubble role="user" text="hủy giùm cô lịch mai" pending />);
    expect(screen.getByText("hủy giùm cô lịch mai")).toBeInTheDocument();
    expect(screen.getByText(/đang gửi lại/i)).toBeInTheDocument();
  });

  it("bot streaming: caret cuối bubble, KHÔNG tạo bubble thứ hai", () => {
    render(<MessageBubble role="bot" text="Dạ mai" streaming />);
    expect(document.querySelectorAll(".bubble").length).toBe(1);
    expect(document.querySelector(".bubble .caret")).not.toBeNull();
  });

  it("user streaming: không có caret", () => {
    render(<MessageBubble role="user" text="đang nói dở" streaming />);
    expect(document.querySelector(".caret")).toBeNull();
  });

  it("không streaming: không có caret", () => {
    render(<MessageBubble role="bot" text="câu đã hoàn chỉnh" />);
    expect(document.querySelector(".caret")).toBeNull();
  });

  it("nhiều dòng: giữ nguyên dấu xuống dòng trong cùng MỘT bong bóng", () => {
    render(
      <MessageBubble
        role="bot"
        text={"Dạ mai 3 giờ chiều còn trống ạ.\nCô muốn làm tóc hay làm nail ạ?"}
      />,
    );
    const el = screen.getByText(/Dạ mai 3 giờ chiều còn trống ạ\./);
    expect(el.textContent).toContain("\n");
    // pre-wrap nằm ở CSS của .bubble — class semantics phải có mặt.
    expect(el.className).toMatch(/bubble/);
  });
});
