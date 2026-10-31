export type BulkFailure = { id: number; message: string }

/**
 * Run one action per id, in order, and keep going when one fails.
 *
 * Sequential on purpose (a burst of parallel writes to the same rows is how a bulk action
 * becomes a pile of lock timeouts). The loops this replaces stopped at the first failure
 * and reported only that one, so everything before it had already happened with nothing on
 * screen to say so -- and retrying hit the first, already-finished item and failed again.
 */
export async function runBulk(ids: number[], action: (id: number) => Promise<unknown>) {
  const failed: BulkFailure[] = []
  let done = 0
  for (const id of ids) {
    try {
      await action(id)
      done += 1
    } catch (error) {
      failed.push({ id, message: error instanceof Error ? error.message : 'Failed' })
    }
  }
  return { done, failed }
}

/** "3 deleted, 2 failed: <why the first one did>" -- the reason is usually the same for all. */
export function bulkSummary(past: string, done: number, failed: BulkFailure[]) {
  return `${done} ${past}, ${failed.length} failed: ${failed[0].message}`
}
