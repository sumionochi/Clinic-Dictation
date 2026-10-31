// Client for the sidecar's development-only demo route (sidecar/src/clinic_sidecar/demo.py).

import { ApiError } from '@/lib/sessions'

export const demoApi = {
  /** Creates a synthetic consultation with a drafted record; returns its session id. */
  create: async (): Promise<string> => {
    const res = await window.api.sidecar.call('POST', '/dev/demo-session')
    if (!res.ok) {
      const detail = (res.body as { detail?: unknown } | null)?.detail
      throw new ApiError(typeof detail === 'string' ? detail : `Request failed (${res.status})`)
    }
    return (res.body as { session_id: string }).session_id
  }
}
