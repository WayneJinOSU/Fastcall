import { fireEvent, render, screen } from '@testing-library/react';
import DateTimeRangePicker from './DateTimeRangePicker';

const props = {
  range: 'custom',
  startAt: '2026-10-31T16:00:00Z',
  endAt: '2026-11-07T16:00:00Z',
};

describe('DateTimeRangePicker', () => {
  it('applies a selected date range once without exposing timezone controls', () => {
    const onApply = vi.fn();
    render(<DateTimeRangePicker {...props} onApply={onApply} />);
    fireEvent.click(screen.getByRole('button', { name: '时间范围' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByText('Asia/Shanghai')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '2026-11-10' }));
    fireEvent.click(screen.getByRole('button', { name: '2026-11-12' }));
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    expect(onApply).toHaveBeenCalledOnce();
    expect(onApply).toHaveBeenCalledWith({
      range: 'custom', start_at: '2026-11-09T16:00:00.000Z', end_at: '2026-11-11T16:00:00.000Z',
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('restores the applied range after cancelling a partial draft', () => {
    render(<DateTimeRangePicker {...props} onApply={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '时间范围' }));
    fireEvent.click(screen.getByRole('button', { name: '2026-11-10' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    fireEvent.click(screen.getByRole('button', { name: '时间范围' }));
    expect(screen.getByLabelText('开始日期')).toHaveValue('2026-11-01');
    expect(screen.getByLabelText('结束日期')).toHaveValue('2026-11-08');
  });

  it('ends yesterday at today midnight instead of the current time', () => {
    const onApply = vi.fn();
    render(<DateTimeRangePicker {...props} onApply={onApply} />);
    fireEvent.click(screen.getByRole('button', { name: '时间范围' }));
    fireEvent.click(screen.getByRole('button', { name: '昨天' }));
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    const value = onApply.mock.calls[0][0];
    expect(value.start_at).toEqual(expect.any(String));
    expect(value.end_at).toEqual(expect.any(String));
    const endLocal = new Date(value.end_at).getTime() + 8 * 60 * 60 * 1000;
    expect(new Date(endLocal).toISOString().slice(11, 16)).toBe('00:00');
  });

  it('rejects an empty time range', () => {
    render(<DateTimeRangePicker {...props} onApply={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '时间范围' }));
    fireEvent.click(screen.getByRole('button', { name: '2026-11-10' }));
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    expect(screen.getByRole('alert')).toHaveTextContent('请选择完整的开始和结束日期');
  });
});
