import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

interface State {
  crashed: boolean;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { crashed: false };

  static getDerivedStateFromError(): State {
    return { crashed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Màn hình hỏng:", error, info.componentStack);
  }

  render() {
    if (!this.state.crashed) return this.props.children;
    return (
      <main className="screen screen--narrow">
        <h1 style={{ fontSize: 26, marginBottom: "var(--s3)" }}>Máy đang trục trặc</h1>
        <p style={{ fontSize: 20, marginBottom: "var(--s4)" }}>
          Cô chú bấm nút bên dưới để mở lại giúp con ạ. Nếu vẫn không được thì gọi cho tiệm nhé.
        </p>
        <button className="btn btn--primary btn--full" onClick={() => window.location.reload()}>
          Mở lại
        </button>
      </main>
    );
  }
}
