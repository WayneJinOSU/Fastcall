import { render, screen } from '@testing-library/react';
import FlowEditor from './FlowEditor';

const config = {
  flow: {
    welcomeNode: { content: 'Hello' },
    endNode: { content: 'Goodbye' },
    noResponse: {},
    voicemailNode: {},
  },
} as any;

describe('FlowEditor', () => {
  it('keeps welcome content and delegates silence to noResponse', () => {
    render(<FlowEditor config={config} onChange={vi.fn()} readOnly={false} />);
    expect(screen.getByDisplayValue('Hello')).toBeInTheDocument();
    expect(screen.queryByText('timeoutReply')).not.toBeInTheDocument();
    expect(screen.getByText('noResponseReply')).toBeInTheDocument();
  });
});
