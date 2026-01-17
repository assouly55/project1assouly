import React from 'react';
import { AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';

type Props = {
  children: React.ReactNode;
  title?: string;
};

type State = {
  hasError: boolean;
  error?: Error;
};

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    // Keep a breadcrumb in the console for debugging
    console.error('UI ErrorBoundary caught an error:', error, errorInfo);
  }

  private handleReload = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="data-card text-center py-10">
          <AlertCircle className="w-10 h-10 text-destructive mx-auto mb-3" />
          <h2 className="text-lg font-medium">{this.props.title || 'Something went wrong'}</h2>
          <p className="text-sm text-muted-foreground mt-2">
            A rendering error occurred. Try reloading the page.
          </p>
          {import.meta.env.DEV && this.state.error?.message && (
            <pre className="mt-4 text-left text-xs bg-muted/40 rounded-lg p-3 overflow-auto">
              {this.state.error.message}
            </pre>
          )}
          <div className="mt-5 flex justify-center">
            <Button onClick={this.handleReload}>Reload</Button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
