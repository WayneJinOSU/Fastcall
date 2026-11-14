import { fireEvent, render, screen } from '@testing-library/react';
import HistoryDrawer from './HistoryDrawer';

const versions = [
  { robot_app_version_id: 'v2', version_no: 2, created_at: '2026-11-10T10:00:00Z', created_by: 'editor' },
  { robot_app_version_id: 'v1', version_no: 1, created_at: '2026-11-09T10:00:00Z', created_by: 'editor' },
] as any;

describe('HistoryDrawer', () => {
  it('selects a historical version without exposing diff or rollback in the drawer', () => {
    const selected = vi.fn();
    render(<HistoryDrawer versions={versions} currentVersionId="v2" onSelect={selected} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /v1/ }));
    expect(selected).toHaveBeenCalledWith(versions[1]);
    expect(screen.queryByText('回滚到此版本')).not.toBeInTheDocument();
    expect(screen.queryByText('Diff')).not.toBeInTheDocument();
  });
});
