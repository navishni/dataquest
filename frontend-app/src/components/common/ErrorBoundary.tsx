import { Component, type ErrorInfo, type ReactNode } from "react";

interface State { error: Error | null }
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };
  static getDerivedStateFromError(error: Error): State { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo): void { console.error(error, info.componentStack); }
  render() {
    if (this.state.error) {
      return (
        <div className="m-8 card card-pad" role="alert">
          <h1 className="text-lg font-semibold">Something went wrong in the interface</h1>
          <p className="mt-2 break-words text-sm text-muted">{this.state.error.message}</p>
          <button className="btn mt-4" onClick={() => this.setState({ error: null })}>Try again</button>
        </div>
      );
    }
    return this.props.children;
  }
}
