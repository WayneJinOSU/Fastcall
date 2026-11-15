import { fireEvent, render, screen } from '@testing-library/react';
import AgentTrendChart from './AgentTrendChart';

describe('AgentTrendChart', () => {
  it('does not connect across null days and renders a single day as a point', () => {
    const { container } = render(<AgentTrendChart title="语音信箱占比" subtitle="测试" days={['2026-11-01', '2026-11-02', '2026-11-03']} series={[{ name: '占比', values: [1, null, 3] }]} unit="%" />);
    expect(container.querySelectorAll('circle')).toHaveLength(2);
    expect(container.querySelectorAll('polyline')).toHaveLength(0);
    expect(container.querySelectorAll('line[stroke="#155eef"]')).toHaveLength(0);
    expect(screen.getByRole('img', { name: '语音信箱占比按天趋势' })).toBeInTheDocument();
  });

  it('renders a single day without forcing a line and shows an honest empty state', () => {
    const { container, rerender } = render(<AgentTrendChart title="平均轮次" subtitle="测试" days={['2026-11-01']} series={[{ name: '轮次', values: [3] }]} />);
    expect(container.querySelectorAll('circle')).toHaveLength(1);
    rerender(<AgentTrendChart title="平均轮次" subtitle="测试" days={['2026-11-01']} series={[{ name: '轮次', values: [null] }]} />);
    expect(screen.getByText('当前时间范围暂无有效指标数据')).toBeInTheDocument();
  });

  it('uses the latest non-empty day for the latest label', () => {
    render(<AgentTrendChart title="语音信箱占比" subtitle="测试" days={['2026-11-01', '2026-11-02']} series={[{ name: '占比', values: [8.6, null] }]} unit="%" />);
    expect(screen.getByText('占比 8.6%')).toBeInTheDocument();
  });

  it('samples date ticks while retaining every data point on long ranges', () => {
    const days = Array.from({ length: 92 }, (_, index) => `2026-11-${String(index + 1).padStart(2, '0')}`);
    const { container } = render(<AgentTrendChart title="会话量" subtitle="测试" days={days} series={[{ name: 'Call', values: days.map(() => 1) }]} />);
    expect(container.querySelectorAll('text')).toHaveLength(13);
    expect(container.querySelectorAll('circle')).toHaveLength(92);
  });

  it('renders y-axis values and shows values on node hover and focus', () => {
    const { container } = render(<AgentTrendChart title="语音信箱占比" subtitle="测试" days={['2026-11-01', '2026-11-02']} series={[{ name: '占比', values: [8.6, 9.2] }]} unit="%" />);
    expect(container.querySelectorAll('.agent-trend-y-ticks text')).toHaveLength(5);
    const node = container.querySelector('circle');
    expect(node).not.toBeNull();
    fireEvent.mouseEnter(node!);
    expect(screen.getByTestId('chart-hover-tooltip')).toHaveTextContent('2026-11-01');
    expect(screen.getByTestId('chart-hover-tooltip')).toHaveTextContent('8.6%');
    fireEvent.blur(node!);
    expect(screen.queryByTestId('chart-hover-tooltip')).not.toBeInTheDocument();
    fireEvent.focus(container.querySelectorAll('circle')[1]);
    expect(screen.getByTestId('chart-hover-tooltip')).toHaveTextContent('9.2%');
  });
});
