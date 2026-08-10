import type { Permission } from './a2a'

export function ApprovalCard({
  permission,
  onAnswer,
}: {
  permission: Permission
  onAnswer: (decision: 'allow' | 'deny') => void
}) {
  return (
    <aside className="approval">
      <p>
        <b>Approval requested:</b> {permission.tool}
      </p>
      <pre>{JSON.stringify(permission.input, null, 2)}</pre>
      <button onClick={() => onAnswer('allow')}>Allow</button>
      <button onClick={() => onAnswer('deny')}>Deny</button>
    </aside>
  )
}
