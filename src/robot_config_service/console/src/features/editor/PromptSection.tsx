import { AppConfig } from '../../api/types';

export default function PromptSection({ config, onChange, readOnly }: { config: AppConfig; onChange: (value: AppConfig) => void; readOnly: boolean }) {
  return (
    <section className="editor-card">
      <div className="editor-card-head"><div><span className="editor-card-title">Prompt</span><span className="editor-card-subtitle">定义 Robot 的角色与回答边界</span></div><span className="character-count">{config.prompt.system_template.length} 字符</span></div>
      <textarea className="prompt-editor" aria-label="系统 Prompt" readOnly={readOnly} value={config.prompt.system_template} onChange={event => onChange({ ...config, prompt: { system_template: event.target.value } })} placeholder="输入系统 Prompt，可使用 {{user_name}} 引用变量" />
      <div className="prompt-foot"><span>可用变量</span>{config.variables.map(variable => <code key={variable.key}>{`{{${variable.key}}}`}</code>)}<span className="prompt-note">发布时校验变量引用</span></div>
    </section>
  );
}
