import { fireEvent, render, screen } from '@testing-library/react';
import PromptSection from './PromptSection';

const config = {
  prompt: { system_template: 'Hello world' },
  variables: [{ key: 'user_name', type: 'string', required: true }],
} as any;

describe('PromptSection', () => {
  it('inserts a variable at the Prompt cursor without replacing content', () => {
    const onChange = vi.fn();
    render(<PromptSection config={config} onChange={onChange} readOnly={false} />);
    const editor = screen.getByLabelText('系统 Prompt') as HTMLTextAreaElement;
    editor.focus();
    editor.setSelectionRange(5, 5);
    fireEvent.select(editor);
    fireEvent.click(screen.getByRole('button', { name: '插入变量 {{user_name}}' }));
    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange.mock.calls[0][0].prompt.system_template).toBe('Hello{{user_name}} world');
    expect(screen.getByRole('status')).toHaveTextContent('已插入 {{user_name}}');
  });

  it('asks for Prompt focus before inserting a variable', () => {
    const onChange = vi.fn();
    render(<PromptSection config={config} onChange={onChange} readOnly={false} />);
    fireEvent.click(screen.getByRole('button', { name: '插入变量 {{user_name}}' }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('请先聚焦 Prompt 输入框');
  });

  it('disables variable insertion for historical read-only versions', () => {
    render(<PromptSection config={config} onChange={vi.fn()} readOnly={true} />);
    expect(screen.getByRole('button', { name: '插入变量 {{user_name}}' })).toBeDisabled();
  });
});
