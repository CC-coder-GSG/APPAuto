import { api } from '../api.js';
import { beginActivity } from './workbench-activity.js';

const pending = new Map();
const queue = [];
const running = new Set();
const controllers = new Set();
let epoch = 0;
export const hydrationEpoch = () => epoch;
export function invalidateHydration() {
  epoch++;
  for (const controller of controllers) controller.abort();
  for (const job of queue.splice(0)) {
    pending.delete(job.key);
    job.resolve({ cancelled: true });
  }
}

async function fetchBatch(kind, ids) {
  const controller = new AbortController();
  controllers.add(controller);
  // Normal reads return partial results within 5s. Allow token renewal too.
  const timer = setTimeout(() => controller.abort(), 35000);
  try {
    const data = await (await api(`/zentao/hydrate/${kind}?ids=${ids.join(',')}`, { signal: controller.signal })).json();
    const errors = new Set((data.__fetch_errors__ || []).map(String));
    return { data, errors, retryable: !data.__auth_required__ };
  } catch (error) {
    return { data: {}, errors: new Set(ids), retryable: !error.status || error.status >= 500 };
  } finally {
    clearTimeout(timer);
    controllers.delete(controller);
  }
}

export function loadHydration(kind, id, priority = 1) {
  id = String(id);
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
  // Each kind has its own lane: slow Bug details cannot block story labels.
  for (const kind of ['stories', 'bugs']) {
    if (running.has(kind)) continue;
    queue.sort((a, b) => a.priority - b.priority);
    const batch = [];
    for (let i = 0; i < queue.length && batch.length < 10;) {
      if (queue[i].kind === kind) batch.push(queue.splice(i, 1)[0]);
      else i++;
    }
    if (!batch.length) continue;
    running.add(kind);
    run(batch).finally(() => { running.delete(kind); pump(); });
  }
}

async function run(batch) {
  const kind = batch[0].kind;
  const activity = beginActivity(kind === 'stories' ? '禅道需求信息更新中' : '禅道 Bug 信息更新中');
  let remaining = batch;
  function settle(result) {
    remaining = remaining.filter(job => {
      if (job.epoch !== epoch) job.resolve({ cancelled: true });
      else if (result.errors.has(job.id)) return true;
      else job.resolve({ data: result.data[job.id] || null });
      return false;
    });
  }
  try {
    const result = await fetchBatch(kind, remaining.map(job => job.id));
    // Deliver successful labels immediately, before retrying failed IDs.
    settle(result);
    if (remaining.length && result.retryable && batch[0].epoch === epoch) {
      await new Promise(resolve => setTimeout(resolve, 400));
      if (batch[0].epoch === epoch) settle(await fetchBatch(kind, remaining.map(job => job.id)));
    }
    remaining.forEach(job => job.resolve(job.epoch === epoch ? { error: true } : { cancelled: true }));
  } catch {
    remaining.forEach(job => job.resolve(job.epoch === epoch ? { error: true } : { cancelled: true }));
  } finally {
    batch.forEach(job => pending.delete(job.key));
    activity.end();
  }
}
