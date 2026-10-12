import { CheckSquare } from 'lucide-react'

export function ApprovalPage() {
  return (
    <div className="p-8 text-center bg-card rounded-lg border border-border">
      <CheckSquare className="w-8 h-8 mx-auto text-primary mb-2" />
      <h2 className="text-base font-bold text-foreground">Approval Queue</h2>
      <p className="text-xs text-muted-foreground mt-1">
        Lead drafts ready for review. Full implementation arriving in Phase B.
      </p>
    </div>
  )
}
