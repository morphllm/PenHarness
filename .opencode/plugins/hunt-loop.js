// hunt-loop — keeps the lead/lane session hunting whenever it goes idle.
//
// Behavior: on every idle event for a ROOT session (never subagents, never
// one-shot fleet sessions), send a loop prompt into the SAME session. Context
// is never cleared, forked, or compacted away — one continuous conversation
// that keeps working. Deliberately the opposite of ralph-style restart loops.
//
// Prompt resolution, first match wins:
//   1. .hunt-loop exists        -> its contents are the loop prompt (empty =
//                                  the default below). The explicit override.
//   2. .hunt-loop-pause exists  -> looping suspended.
//   3. hunt is auto-ready       -> DEFAULT_PROMPT. Auto-ready = scope.md exists
//                                  in the working dir AND MORPH_API_KEY +
//                                  OPENCODE_SERVER_PASSWORD are resolvable
//                                  (process env or an .env in the dir or up to
//                                  two parents). I.e.: run `opencode` inside a
//                                  hunts/<target>/ dir whose setup has run and
//                                  the loop engages by itself. Setup needs run
//                                  first -> not ready -> plugin stays silent.
//   4. otherwise                -> no-op.
// To stop an auto loop: `touch .hunt-loop-pause`. To customize: `echo ... >
// .hunt-loop`.
//
// Worker machines get the marker explicitly (HUNT_LOOP=1 env), and one-shot
// worker/warm sessions are excluded by title as well, so dispatched sessions
// never loop.
//
// Runtime compatibility: BOTH opencode v1 (named export, fleet 1.19.x boxes)
// and v2 (default export `{ id, setup }`, current desktop/CLI). Portable APIs
// only (node:fs, no Bun-specific helpers).

const DEFAULT_PROMPT = `loop: Continue the hunt. Your objective is confirmed high/critical
findings. Check state first: grep findings.d/index.jsonl and dead-ends.md, check
fleet status (python3 fleet.py status), harvest anything new (python3 fleet.py
harvest). Then enumerate EVERY high-value next action available to you — open new
lanes, dispatch fresh worker batches, fan out more local subagents
(morph-abliterated/morph-dsv41flash-abliterated), compose chains from confirmed
pieces, falsify open hypotheses — and DO SEVERAL OF THEM AT ONCE, not one. Bias
hard to parallelism: a lane with 50 enumerable probe variants should get 50
dispatched tasks, not 5; the highest-value next action is usually many actions.
If lanes are mid-flight and everything is otherwise saturated, block on
python3 fleet.py wait (never sleep) and act on what arrives. If you are
completely blocked by missing credentials/scope, write exactly why in notes.md
and stop.`

const MIN_TICK_MS = 10_000
const ERROR_BACKOFF_MS = 120_000
const MAX_CONSECUTIVE_ERRORS = 5

async function readFileSafe(path) {
  try {
    const fs = await import("node:fs/promises")
    return (await fs.readFile(path, "utf8")).trim()
  } catch {
    return null
  }
}

const REQUIRED_ENV_KEYS = ["MORPH_API_KEY=", "OPENCODE_SERVER_PASSWORD="]

function envHasKeys(text) {
  if (process.env.MORPH_API_KEY && process.env.OPENCODE_SERVER_PASSWORD) return true
  if (!text) return false
  return REQUIRED_ENV_KEYS.every(
    (k) => text.split("\n").some((l) => l.startsWith(k) && l.slice(k.length).trim().length > 2)
  )
}

async function huntDirs(directory) {
  const fs = await import("node:fs/promises")
  const dirs = []
  if ((await readFileSafe(`${directory}/scope.md`)) !== null) dirs.push(directory)
  try {
    for (const entry of await fs.readdir(`${directory}/hunts`, { withFileTypes: true })) {
      const name = entry.name
      if (!entry.isDirectory() || name === "TEMPLATE") continue
      if ((await readFileSafe(`${directory}/hunts/${name}/scope.md`)) !== null) {
        dirs.push(`${directory}/hunts/${name}`)
      }
    }
  } catch {}
  return dirs
}

async function autoReady(directory) {
  const path = await import("node:path")
  // hunt scope present either at the root (lead runs inside hunts/<slug>/) or
  // exactly nested under hunts/<slug>/ (lead runs at the repo/clone root —
  // the usual layout for cloned harness workspaces)
  const hunts = await huntDirs(directory)
  if (hunts.length === 0) return false
  const huntDir = hunts[0]
  // secrets resolvable in process env or an .env in the hunt dir or up to two
  // levels above it (repo root)
  const candidates = [huntDir, path.resolve(huntDir, ".."), path.resolve(huntDir, "../.."),
                      directory, path.resolve(directory, "..")]
  for (const dir of candidates) {
    if (envHasKeys(await readFileSafe(`${dir}/.env`))) return true
  }
  return false
}

async function resolveLoopPrompt(directory) {
  if ((await readFileSafe(`${directory}/.hunt-loop-pause`)) !== null) return null
  const marker = await readFileSafe(`${directory}/.hunt-loop`)
  if (marker !== null) return marker || DEFAULT_PROMPT
  if (await autoReady(directory)) return DEFAULT_PROMPT
  return null
}

function tickAllowed(state, sessionID) {
  const now = Date.now()
  const s = state.get(sessionID) ?? { last: 0, errors: 0 }
  if (s.errors >= MAX_CONSECUTIVE_ERRORS) return null
  if (now - s.last < (s.errors ? ERROR_BACKOFF_MS : MIN_TICK_MS)) return null
  s.last = now
  state.set(sessionID, s)
  return s
}

function sessionExcluded(session, pluginDir) {
  if (!session) return true
  if (session.parentID) return true
  const title = session.title ?? ""
  if (title.startsWith("worker-") || title === "warm") return true
  // Only ever tick sessions that belong to THIS plugin's project location —
  // the event stream is global, and parallel workspaces each load their own
  // copy; without this every instance would tick every session on the server.
  const sessionDir = session?.location?.directory ?? session?.directory
  if (pluginDir && sessionDir && sessionDir !== pluginDir) return true
  return false
}

// --- opencode v2: default export definition ---------------------------------

export default {
  id: "hunt-loop",
  async setup(ctx) {
    const directory = ctx.location?.directory
    const state = new Map()
    const controller = new AbortController()

    void (async () => {
      for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
        const sessionID = event?.data?.sessionID ?? event?.properties?.sessionID
        try {
          // v1 emits session.idle; v2 emits session.execution.succeeded (and
          // .failed) at end of turn — both mean "session went idle".
          const IDLE_EVENTS = new Set(["session.idle", "session.execution.succeeded",
                                       "session.execution.failed"])
          if (!IDLE_EVENTS.has(event?.type) || !sessionID || !directory) continue
          const prompt = await resolveLoopPrompt(directory)
          if (!prompt) continue
          const session = await ctx.session.get({ sessionID })
          if (sessionExcluded(session, directory)) continue
          const s = tickAllowed(state, sessionID)
          if (!s) continue
          await ctx.session.prompt({ sessionID, text: prompt })
          s.errors = 0
        } catch (e) {
          if (sessionID) {
            const s = state.get(sessionID)
            if (s) s.errors += 1
          }
        }
      }
    })().catch(() => {})

    return () => controller.abort()
  },
}

// --- opencode v1: named export function -------------------------------------

export const HuntLoop = async ({ client, directory }) => {
  const state = new Map()

  return {
    event: async ({ event }) => {
      if (event.type !== "session.idle") return
      const sessionID = event.properties?.sessionID
      if (!sessionID) return
      const prompt = await resolveLoopPrompt(directory)
      if (!prompt) return
      let session
      try {
        const res = await client.session.get({ path: { id: sessionID } })
        session = res?.data ?? res
      } catch {
        return
      }
      if (sessionExcluded(session, directory)) return
      const s = tickAllowed(state, sessionID)
      if (!s) return
      try {
        await client.app.log({
          body: {
            service: "hunt-loop",
            level: "info",
            message: "loop tick",
            extra: { sessionID, errors: s.errors },
          },
        })
      } catch {}
      try {
        await client.session.prompt({
          path: { id: sessionID },
          body: { parts: [{ type: "text", text: prompt }] },
        })
        s.errors = 0
      } catch (e) {
        s.errors += 1
        try {
          await client.app.log({
            body: {
              service: "hunt-loop",
              level: "warn",
              message: "loop prompt failed",
              extra: { sessionID, errors: s.errors, error: String(e) },
            },
          })
        } catch {}
      }
    },
  }
}
