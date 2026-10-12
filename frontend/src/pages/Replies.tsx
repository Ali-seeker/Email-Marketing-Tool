import { MessageSquare } from 'lucide-react'

export function RepliesPage() {
  return (
    <div className="p-8 text-center bg-card rounded-lg border border-border">
      <MessageSquare className="w-8 h-8 mx-auto text-primary mb-2" />
      <h2 className="text-base font-bold text-foreground">Inbound Replies Inbox</h2>
      <p className="text-xs text-muted-foreground mt-1">
        Categorized replies and threads. Full implementation arriving in Phase B.
      </p>
    </div>
  )
}
