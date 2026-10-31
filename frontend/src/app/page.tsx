'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { RadioTower } from 'lucide-react'
import { useAuthStore } from '@/lib/store'
import { destinationFor } from '@/lib/roles'

export default function Home() {
  const router = useRouter()
  const { token, user, hydrated } = useAuthStore()

  useEffect(() => {
    if (hydrated) {
      if (token && user) {
        router.replace(destinationFor(user))
      } else {
        router.replace('/login')
      }
    }
  }, [hydrated, token, user, router])

  return (
    <main className="grid min-h-screen place-items-center bg-rail text-brand">
      <div className="flex flex-col items-center gap-1.5 text-center">
        <div className="flex items-center gap-3 text-sm font-semibold tracking-[0.16em]">
          <RadioTower className="size-5 animate-pulse motion-reduce:animate-none" /> MR SIGNAGE
        </div>
        <span className="text-[11px] font-medium tracking-wide text-brand/70">(Midhun-Roshan)</span>
      </div>
    </main>
  )
}
