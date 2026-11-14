import { useRef, useState } from 'react';
import { AppConfig } from '../../api/types';

export default function PromptSection({ config, onChange, readOnly }: { config: AppConfig; onChange: (value: AppConfig) => void; readOnly: boolean }) {
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const selectionRef = useRef<{ start: number; end: number } | null>(null);
  const [insertStatus, setInsertStatus] = useState('');

  function rememberSelection(input: HTMLTextAreaElement) {
    selectionRef.current = {
      start: input.selectionStart ?? input.value.length,
      end: input.selectionEnd ?? input.value.length,
    };
  }

  function insertVariable(key: string) {
    const input = editorRef.current;
    const selection = selectionRef.current;
    if (!input || !selection) {
      setInsertStatus('请先聚焦 Prompt 输入框');
      return;
    }
    const token = `{{${key}}}`;
    const systemTemplate = `${input.value.slice(0, selection.start)}${token}${input.value.slice(selection.end)}`;
    const cursor = selection.start + token.length;
    onChange({ ...config, prompt: { system_template: systemTemplate } });
    setInsertStatus(`已插入 ${token}`);
    requestAnimationFrame(() => {
      input.focus();
      input.setSelectionRange(cursor, cursor);
      rememberSelection(input);
    });
  }

  return (
    <section className="editor-card">
      <div className="editor-card-head"><div><span className="editor-card-title">Prompt</span><span className="editor-card-subtitle">定义 Robot 的角色与回答边界</span></div><span className="character-count">{config.prompt.system_template.length} 字符</span></div>
      <textarea
        ref={editorRef}
        className="prompt-editor"
        aria-label="系统 Prompt"
        readOnly={readOnly}
        value={config.prompt.system_template}
        onFocus={event => rememberSelection(event.currentTarget)}
        onSelect={event => rememberSelection(event.currentTarget)}
        onChange={event => {
          rememberSelection(event.currentTarget);
          onChange({ ...config, prompt: { system_template: event.target.value } });
        }}
        placeholder="输入系统 Prompt，可使用 {{user_name}} 引用变量"
      />
      <div className="prompt-foot"><span>可用变量</span><div className="prompt-variable-chips">{config.variables.map(variable => <button type="button" className="flow-variable-chip" key={variable.key} disabled={readOnly} aria-label={`插入变量 {{${variable.key}}}`} onMouseDown={event => event.preventDefault()} onClick={() => insertVariable(variable.key)}>{`{{${variable.key}}}`}</button>)}</div><span className="prompt-note" role="status">{insertStatus || '点击标签插入到 Prompt 光标处'}</span></div>
    </section>
  );
}
