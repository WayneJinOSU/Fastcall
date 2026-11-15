export default function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div className="alert error" role="alert"><span>{message}</span>{onRetry && <button className="button small" onClick={onRetry}>重试</button>}</div>;
}
