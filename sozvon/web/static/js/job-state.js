const terminal = new Set(['done', 'completed', 'succeeded', 'success', 'failed', 'error', 'cancelled', 'canceled', 'stopped', 'interrupted']);
export const isActiveJob = job => Boolean(job && !terminal.has(job.status));
