import { AppConfig, VariableDefinition, VariableType } from '../../api/types';

const types: VariableType[] = ['string', 'decimal', 'integer', 'boolean', 'datetime'];

export default function VariablesSection({ config, onChange, readOnly }: { config: AppConfig; onChange: (value: AppConfig) => void; readOnly: boolean }) {
  function update(index: number, patch: Partial<VariableDefinition>) {
    const variables = config.variables.map((variable, itemIndex) => itemIndex === index ? { ...variable, ...patch } : variable);
    onChange({ ...config, variables });
  }
  return (
    <section className="editor-card">
      <div className="editor-card-head"><div><span className="editor-card-title">变量</span><span className="editor-card-subtitle">仅保存定义，不保存真实通话值</span></div>{!readOnly && <button className="button small" onClick={() => onChange({ ...config, variables: [...config.variables, { key: `variable_${config.variables.length + 1}`, type: 'string', required: false }] })}>添加变量</button>}</div>
      {config.variables.length === 0 ? <div className="inline-empty">暂无变量</div> : <div className="table-scroll"><table className="variables-table"><thead><tr><th>key</th><th>type</th><th>required</th><th>sensitive</th><th>max_length</th><th>default</th>{!readOnly && <th />}</tr></thead><tbody>{config.variables.map((variable, index) => <tr key={index}>
        <td><input aria-label={`变量 ${index + 1} key`} readOnly={readOnly} value={variable.key} onChange={event => update(index, { key: event.target.value })} /></td>
        <td><select aria-label={`变量 ${index + 1} type`} disabled={readOnly} value={variable.type} onChange={event => update(index, { type: event.target.value as VariableType })}>{types.map(type => <option key={type}>{type}</option>)}</select></td>
        <td><input type="checkbox" aria-label={`变量 ${index + 1} required`} disabled={readOnly} checked={variable.required} onChange={event => update(index, { required: event.target.checked, ...(event.target.checked ? { default: undefined } : {}) })} /></td>
        <td><input type="checkbox" aria-label={`变量 ${index + 1} sensitive`} disabled={readOnly} checked={variable.sensitive || false} onChange={event => update(index, { sensitive: event.target.checked })} /></td>
        <td><input type="number" min="1" aria-label={`变量 ${index + 1} max length`} readOnly={readOnly} value={variable.max_length || ''} onChange={event => update(index, { max_length: event.target.value ? Number(event.target.value) : undefined })} /></td>
        <td><input aria-label={`变量 ${index + 1} default`} readOnly={readOnly || variable.required} value={variable.default || ''} onChange={event => update(index, { default: event.target.value })} placeholder={variable.required ? '必填变量无默认值' : ''} /></td>
        {!readOnly && <td><button className="icon-delete" aria-label={`删除变量 ${variable.key}`} onClick={() => onChange({ ...config, variables: config.variables.filter((_, itemIndex) => itemIndex !== index) })}>×</button></td>}
      </tr>)}</tbody></table></div>}
    </section>
  );
}
