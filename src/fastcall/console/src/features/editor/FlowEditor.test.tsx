import { fireEvent, render, screen } from '@testing-library/react';
import FlowEditor from './FlowEditor';

const config = {
  variables: [{ key: 'user_name', type: 'string', required: true }],
  flow: {
    welcomeNode: { content: 'Hello' },
    endNode: { content: 'Goodbye' },
    noResponse: {},
    voicemailNode: {},
  },
} as any;

function renderEditor(onChange = vi.fn(), onManageVariables = vi.fn()) {
  render(<FlowEditor config={config} onChange={onChange} onManageVariables={onManageVariables} readOnly={false} />);
  return { onChange, onManageVariables };
}

describe('FlowEditor', () => {
  it('keeps welcome content and delegates silence to noResponse', () => {
    renderEditor();
    expect(screen.getByDisplayValue('Hello')).toBeInTheDocument();
    expect(screen.queryByText('timeoutReply')).not.toBeInTheDocument();
    expect(screen.getByText('noResponseReply')).toBeInTheDocument();
  });

  it('inserts a shared variable at the focused input cursor without replacing content', () => {
    const { onChange } = renderEditor();
    const input = screen.getByDisplayValue('Hello') as HTMLInputElement;
    input.focus();
    input.setSelectionRange(2, 2);
    fireEvent.select(input);
    fireEvent.click(screen.getByRole('button', { name: '插入变量 {{user_name}}' }));
    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange.mock.calls[0][0].flow.welcomeNode.content).toBe('He{{user_name}}llo');
    expect(screen.getByRole('status')).toHaveTextContent('已插入 {{user_name}}');
  });

  it('asks for a target input before inserting when no field has focus', () => {
    const { onChange } = renderEditor();
    fireEvent.click(screen.getByRole('button', { name: '插入变量 {{user_name}}' }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('请先选择要插入变量的文本框');
  });

  it('returns to Prompt variables through the manage action', () => {
    const { onManageVariables } = renderEditor();
    fireEvent.click(screen.getByRole('button', { name: '管理变量 →' }));
    expect(onManageVariables).toHaveBeenCalledOnce();
  });
});
