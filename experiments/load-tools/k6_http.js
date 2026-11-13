// k6 at a fixed request rate: one GET per iteration, RATE iterations per second for DURATION, at most CONNECTIONS
// virtual users (each keeps one keep-alive connection). run.py sets the environment and reads SUMMARY.
import http from 'k6/http';

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['p(50)', 'p(99)', 'avg', 'max'],
  scenarios: {
    fixed: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RATE),
      timeUnit: '1s',
      duration: __ENV.DURATION,
      preAllocatedVUs: Number(__ENV.CONNECTIONS),
      maxVUs: Number(__ENV.CONNECTIONS),
    },
  },
};

export default function () {
  http.get(__ENV.URL, { timeout: '5s' });
}

export function handleSummary(data) {
  return { [__ENV.SUMMARY]: JSON.stringify(data) };
}
