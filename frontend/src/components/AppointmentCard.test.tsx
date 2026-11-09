import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AppointmentCard } from "./AppointmentCard";
import type { AppointmentCardData } from "./AppointmentCard";

const base: AppointmentCardData = {
  id: "a1",
  start_at: "2099-08-07T09:00:00+07:00",
  end_at: "2099-08-07T11:00:00+07:00",
  note: null,
  user_name: "Cô Lan",
  phone: "0912345678",
  services: [],
};

describe("AppointmentCard", () => {
  it("hiện tên dịch vụ và giờ xong bằng mốc", () => {
    render(
      <AppointmentCard
        appointment={{ ...base, services: [{ id: "toc-nhuom", name: "Nhuộm" }] }}
      />,
    );
    expect(screen.getByText("Nhuộm")).toBeInTheDocument();
    expect(screen.getByText(/xong lúc/i)).toBeInTheDocument();
    // Mốc giờ, không đếm ngược kiểu "còn X phút".
    expect(screen.queryByText(/còn \d+ phút/i)).not.toBeInTheDocument();
  });

  it("không có dịch vụ thì không hiện dòng dịch vụ", () => {
    render(<AppointmentCard appointment={{ ...base, services: [] }} />);
    expect(screen.queryByText("Nhuộm")).not.toBeInTheDocument();
    expect(screen.getByText(/xong lúc/i)).toBeInTheDocument();
  });
});
