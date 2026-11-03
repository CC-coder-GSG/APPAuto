import { api } from '../api.js';
import { beginActivity } from './workbench-activity.js';

const pending = new Map();
const queue = [];
let running = 0;
let epoch = 0;
export const hydrationEpoch = () => epoch;
export function invalidateHydration() { epoch++; }

async function fetchBatch(kind, ids) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const data = await (await api(`/zentao/hydrate/${kind}?ids=${ids.join(',')}`, { signal: controller.signal })).json();
    const errors = new Set((data.__fetch_errors__ || []).map(String));
    return { data, errors, retryable: !data.__auth_required__ };
  } catch (error) {
    return { data: {}, errors: new Set(ids), retryable: !error.status || error.status >= 500 };
  } finally { clearTimeout(timer); }
}

export function loadHydration(kind, id, priority = 1) {
  const key = `${epoch}:${kind}:${id}`;
  let job = pending.get(key);
  if (!job) {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    job = { kind, id, priority, key, epoch, promise, resolve };
    pending.set(key, job);
    queue.push(job);
    queueMicrotask(pump);
  } else job.priority = Math.min(priority, job.priority);
  return job.promise;
}

function pump() {
  while (running < 2 && queue.length) {
    queue.sort((a, b) => a.priority - b.priority);
    const first = queue.shift();
    const batch = [first];
    for (let i = 0; i < queue.length && batch.length < 20;) {
      if (queue[i].kind === first.kind && queue[i].epoch === first.epoch) batch.push(queue.splice(i, 1)[0]);
      else i++;
    }
    if (first.epoch !== epoch) {
      batch.forEach(job => { pending.delete(job.key); job.resolve({ cancelled: true }); });
      continue;
    }
    running++;
    run(batch).finally(() => { running--; pump(); });
  }
}

async function run(batch) {
  const kind = batch[0].kind;
  const activity = beginActivity(kind === 'stories' ? '禅道需求信息更新中' : '禅道 Bug 信息更新中');
  try {
    let result = await fetchBatch(kind, batch.map(job => job.id));
    if (result.errors.size && result.retryable && batch[0].epoch === epoch) {
      await new Promise(resolve => setTimeout(resolve, 400));
      const retry = await fetchBatch(kind, [...result.errors]);
      result = { data: { ...result.data, ...retry.data }, errors: retry.errors };
    }
    for (const job of batch) {
      if (job.epoch !== epoch) job.resolve({ cancelled: true });
      else if (result.errors.has(job.id)) job.resolve({ error: true });
      else job.resolve({ data: result.data[job.id] || null });
    }
  } catch {
    batch.forEach(job => job.resolve({ error: true }));
  } finally {
    batch.forEach(job => pending.delete(job.key));
    activity.end();
  }
}
