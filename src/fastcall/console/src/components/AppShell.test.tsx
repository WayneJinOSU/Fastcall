import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AppShell from './AppShell';

describe('AppShell', () => {
  it('derives active navigation and breadcrumbs from the route', () => {
    render(<MemoryRouter initialEntries={['/reports/agent']}><AppShell actorId="admin"><div>内容</div></AppShell></MemoryRouter>);
    expect(screen.getByRole('link', { name: '通话技术分析' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: '外呼任务' })).not.toHaveAttribute('aria-current', 'page');
    expect(screen.getAllByText('技术观测')).toHaveLength(2);
    expect(screen.getByText('admin')).toBeInTheDocument();
  });

  it('keeps Robot navigation active on the editor route', () => {
    render(<MemoryRouter initialEntries={['/apps/app_1/edit']}><AppShell actorId="editor"><div>内容</div></AppShell></MemoryRouter>);
    expect(screen.getByRole('link', { name: 'Robot 应用' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByText('编辑配置')).toBeInTheDocument();
  });
});
