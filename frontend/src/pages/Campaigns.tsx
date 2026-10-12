import { Send } from 'lucide-react'

export function CampaignsPage() {
  return (
    <div className="p-8 text-center bg-card rounded-lg border border-border">
      <Send className="w-8 h-8 mx-auto text-primary mb-2" />
      <h2 className="text-base font-bold text-foreground">Campaigns & Sequence Editor</h2>
      <p className="text-xs text-muted-foreground mt-1">
        Campaign management and step sequence editor. Full implementation arriving in Phase B.
      </p>
    </div>
  )
}
