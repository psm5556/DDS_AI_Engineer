import { Component, type ReactNode } from "react";

/** 화면 일부에서 오류가 나도 페이지 전체가 하얗게 사라지지 않도록, 그 부분만 안내 문구로 바꾼다. */
export class ErrorBoundary extends Component<{ children: ReactNode; label?: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="notice warn" role="alert">
        {this.props.label ?? "이 부분"}을(를) 표시하지 못했습니다.{" "}
        <button className="link-btn" onClick={() => this.setState({ error: null })}>다시 시도</button>
      </div>
    );
  }
}
