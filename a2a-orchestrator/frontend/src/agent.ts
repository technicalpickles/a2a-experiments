import { HttpAgent } from '@copilotkit/react-core/v2'
import type { BaseEvent, RunAgentInput } from '@copilotkit/react-core/v2'
import type { Observable } from 'rxjs'

// CopilotChat fires connectAgent() on every mount, expecting
// RUN_STARTED → MESSAGES_SNAPSHOT → RUN_FINISHED from the agent's
// connect(). Plain HttpAgent has no connect(), and the library swallows
// the miss silently — that silent no-op was the empty pane on reload.
// This answers it: same wire shape as run, aimed at /agui/connect.
export class ReplayHttpAgent extends HttpAgent {
  protected connect(input: RunAgentInput): Observable<BaseEvent> {
    const replay = new HttpAgent({ url: '/agui/connect', headers: this.headers })
    return replay.run(input)
  }
}
