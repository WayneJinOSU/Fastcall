interface PaginationProps {
  hasPrevious: boolean;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  note: string;
}

export default function Pagination({ hasPrevious, hasNext, onPrevious, onNext, note }: PaginationProps) {
  return <div className="pagination"><span>{note}</span><div className="pagination-actions"><button className="button small" disabled={!hasPrevious} onClick={onPrevious}>上一页</button><button className="button small" disabled={!hasNext} onClick={onNext}>下一页</button></div></div>;
}
