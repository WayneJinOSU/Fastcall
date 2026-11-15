export default function EmptyState({ children = '暂无数据' }: { children?: React.ReactNode }) {
  return <div className="empty-state">{children}</div>;
}
