import type { ReactNode } from 'react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

interface PlannedItem {
  step: number
  text: string
}

interface ScreenLayoutProps {
  title: string
  description: string
  planned: PlannedItem[]
  children?: ReactNode
}

export function ScreenLayout({
  title,
  description,
  planned,
  children
}: ScreenLayoutProps): React.JSX.Element {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="text-sm text-muted-foreground">{description}</p>
      </header>

      {children}

      <Card>
        <CardHeader>
          <CardTitle>Not built yet</CardTitle>
          <CardDescription>This screen is a placeholder. It gets filled in by:</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-col gap-2 text-sm">
            {planned.map((item) => (
              <li key={item.text} className="flex items-start gap-3">
                <Badge variant="outline" className="shrink-0">
                  Step {item.step}
                </Badge>
                <span>{item.text}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}
