// Adapted from TranscriptionSuite (https://github.com/homelab-00/TranscriptionSuite) —
// dashboard/electron/mlxServerManager.ts: spawning the venv's `python -m uvicorn`,
// adding Homebrew to PATH for Finder-launched apps, and the SIGTERM-then-SIGKILL stop.
// Changed here: binds to 127.0.0.1 only, uses a random port and a per-launch auth token,
// and treats the sidecar as ready when /health answers rather than on a log line.

import { ChildProcess, spawn } from 'child_process'
import { randomBytes } from 'crypto'
import { app } from 'electron'
import * as fs from 'fs'
import { createServer } from 'net'
import * as path from 'path'

import type { SidecarResponse, SidecarState } from '../shared/sidecar'

const HOST = '127.0.0.1'
const STARTUP_TIMEOUT_MS = 30_000
const HEALTH_POLL_MS = 250
const STOP_TIMEOUT_MS = 5_000
const MAX_LOG_LINES = 300
// Hugging Face cache folders and weight files of the models the sidecar uses:
// transcriber.py FINAL_MODEL and diarizer.py DIARIZATION_MODEL.
const REQUIRED_MODELS: [cacheDir: string, weights: string][] = [
  ['models--mlx-community--whisper-large-v3-mlx', 'weights.npz'],
  ['models--mlx-community--diar_sortformer_4spk-v1-fp32', 'model.safetensors']
]

export class SidecarManager {
  private process: ChildProcess | null = null
  private token = ''
  private logs: string[] = []
  private state: SidecarState = { status: 'stopped', port: null, version: null, error: null }

  constructor(private readonly onChange: (state: SidecarState) => void) {}

  getState(): SidecarState {
    return this.state
  }

  getLogs(): string[] {
    return [...this.logs]
  }

  async start(): Promise<void> {
    if (this.state.status === 'running' || this.state.status === 'starting') return
    if (this.process) await this.stop()

    const sidecarDir = this.sidecarDir()
    const python = path.join(sidecarDir, '.venv', 'bin', 'python')
    if (!fs.existsSync(python)) {
      this.fail(`Python environment not found. Run \`uv sync\` in ${sidecarDir} first.`)
      return
    }

    const modelsDir = path.join(app.getPath('userData'), 'models')
    const modelCached = REQUIRED_MODELS.every(([dir, weights]) =>
      isModelDownloaded(path.join(modelsDir, 'hub', dir), weights)
    )
    const port = await findFreePort()
    this.token = randomBytes(32).toString('hex')
    this.setState({ status: 'starting', port, version: null, error: null })
    this.log(`[sidecar] starting on ${HOST}:${port}`)

    // Apps launched from Finder only get /usr/bin:/bin:/usr/sbin:/sbin, so tools
    // like ffmpeg from Homebrew would be missing without this.
    const inheritedPath = process.env.PATH ?? '/usr/bin:/bin:/usr/sbin:/sbin'
    const spawnPath = inheritedPath.includes('/opt/homebrew')
      ? inheritedPath
      : `/opt/homebrew/bin:/usr/local/bin:${inheritedPath}`

    const child = spawn(
      python,
      [
        '-m',
        'uvicorn',
        'clinic_sidecar.main:create_app',
        '--factory',
        '--host',
        HOST,
        '--port',
        String(port)
      ],
      {
        cwd: sidecarDir,
        env: {
          ...process.env,
          PATH: spawnPath,
          SIDECAR_TOKEN: this.token,
          CLINIC_DATA_DIR: path.join(app.getPath('userData'), 'data'),
          // Speech models are downloaded here on first use (Hugging Face cache layout).
          HF_HOME: modelsDir,
          HF_HUB_DISABLE_TELEMETRY: '1',
          // Once the models are on disk, stop Hugging Face from checking for updates,
          // so a consultation never causes network traffic.
          ...(modelCached ? { HF_HUB_OFFLINE: '1' } : {}),
          // Without this, output sits in an 8 KB pipe buffer instead of reaching us.
          PYTHONUNBUFFERED: '1'
        },
        stdio: ['ignore', 'pipe', 'pipe']
      }
    )
    this.process = child

    child.stdout?.on('data', (data: Buffer) => this.logChunk(data, ''))
    child.stderr?.on('data', (data: Buffer) => this.logChunk(data, '[stderr] '))
    child.on('error', (err) => {
      this.process = null
      this.fail(`Could not start Python: ${err.message}`)
    })
    child.on('exit', (code, signal) => {
      this.process = null
      const reason = code !== null ? `exit code ${code}` : `signal ${signal}`
      this.log(`[sidecar] exited (${reason})`)
      if (this.state.status === 'stopping') {
        this.setState({ status: 'stopped', port: null, version: null, error: null })
      } else if (this.state.status !== 'error') {
        this.fail(`The engine stopped unexpectedly (${reason}).`)
      }
    })

    await this.waitUntilHealthy(child)
  }

  async stop(): Promise<void> {
    const child = this.process
    if (!child) {
      this.setState({ status: 'stopped', port: null, version: null, error: null })
      return
    }
    this.setState({ ...this.state, status: 'stopping' })
    await new Promise<void>((resolve) => {
      const killTimer = setTimeout(() => child.kill('SIGKILL'), STOP_TIMEOUT_MS)
      child.once('exit', () => {
        clearTimeout(killTimer)
        resolve()
      })
      child.kill('SIGTERM')
    })
  }

  async restart(): Promise<void> {
    await this.stop()
    await this.start()
  }

  /** Authenticated request to the sidecar. Only the main process holds the token. */
  async request(route: string, init: RequestInit = {}): Promise<Response> {
    if (!this.state.port) throw new Error('Sidecar is not running')
    const headers = new Headers(init.headers)
    headers.set('Authorization', `Bearer ${this.token}`)
    return fetch(`http://${HOST}:${this.state.port}${route}`, { ...init, headers })
  }

  /**
   * JSON request on behalf of the renderer. Inputs come over IPC, so they are
   * checked here rather than trusted: known methods only, and a plain path.
   */
  async callJson(method: unknown, route: unknown, body: unknown): Promise<SidecarResponse> {
    if (method !== 'GET' && method !== 'POST' && method !== 'DELETE') {
      throw new Error(`Method not allowed: ${String(method)}`)
    }
    if (typeof route !== 'string' || !/^\/[A-Za-z0-9/_-]*$/.test(route)) {
      throw new Error(`Invalid route: ${String(route)}`)
    }
    const res = await this.request(route, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    })
    return { ok: res.ok, status: res.status, body: await parseBody(res) }
  }

  /** Raw PCM upload for a recording; ids and payload are checked like callJson's inputs. */
  async postAudio(
    sessionId: unknown,
    recordingId: unknown,
    pcm: unknown
  ): Promise<SidecarResponse> {
    const isId = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{32}$/.test(v)
    if (!isId(sessionId) || !isId(recordingId)) throw new Error('Invalid recording id')
    if (!(pcm instanceof ArrayBuffer)) throw new Error('Audio must be an ArrayBuffer')
    return this.callRaw(
      `/sessions/${sessionId}/recordings/${recordingId}/audio`,
      Buffer.from(pcm),
      'application/octet-stream'
    )
  }

  private async callRaw(
    route: string,
    body: Buffer,
    contentType: string
  ): Promise<SidecarResponse> {
    const res = await this.request(route, {
      method: 'POST',
      headers: { 'Content-Type': contentType },
      body: new Uint8Array(body)
    })
    return { ok: res.ok, status: res.status, body: await parseBody(res) }
  }

  private async waitUntilHealthy(child: ChildProcess): Promise<void> {
    const deadline = Date.now() + STARTUP_TIMEOUT_MS
    while (Date.now() < deadline) {
      // Gave up already: the process exited or a newer start replaced it.
      if (this.process !== child || this.state.status !== 'starting') return
      try {
        const res = await this.request('/health', { signal: AbortSignal.timeout(1_000) })
        if (res.ok) {
          const body = (await res.json()) as { version?: string }
          this.log(`[sidecar] healthy (version ${body.version ?? 'unknown'})`)
          this.setState({ ...this.state, status: 'running', version: body.version ?? null })
          return
        }
      } catch {
        // Not listening yet.
      }
      await new Promise((r) => setTimeout(r, HEALTH_POLL_MS))
    }
    this.fail(`Sidecar did not answer /health within ${STARTUP_TIMEOUT_MS / 1000} s`)
    child.kill('SIGKILL')
  }

  private sidecarDir(): string {
    // In development app.getAppPath() is <repo>/app, so the sidecar sits next to it.
    // Packaged builds will need a bundled path; that comes with packaging.
    return path.resolve(app.getAppPath(), '..', 'sidecar')
  }

  private fail(error: string): void {
    this.log(`[sidecar] error: ${error}`)
    this.setState({ status: 'error', port: null, version: null, error })
  }

  private setState(next: SidecarState): void {
    this.state = next
    this.onChange(next)
  }

  private logChunk(data: Buffer, prefix: string): void {
    for (const line of data.toString().split('\n')) {
      if (line.trim()) this.log(prefix + line)
    }
  }

  private log(line: string): void {
    this.logs.push(line)
    if (this.logs.length > MAX_LOG_LINES) this.logs = this.logs.slice(-MAX_LOG_LINES)
    console.log(line)
  }
}

/** True once a snapshot with the weights file exists, so a half-finished download doesn't count. */
function isModelDownloaded(cacheDir: string, weights: string): boolean {
  const snapshots = path.join(cacheDir, 'snapshots')
  if (!fs.existsSync(snapshots)) return false
  return fs.readdirSync(snapshots).some((rev) => fs.existsSync(path.join(snapshots, rev, weights)))
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text()
  try {
    return text ? JSON.parse(text) : null
  } catch {
    return { detail: text }
  }
}

function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, HOST, () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close(() => resolve(port))
    })
  })
}
