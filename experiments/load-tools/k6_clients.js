// k6 with a fixed number of clients (closed model): CLIENTS virtual users, each sends its next GET as soon as the
// last one is answered, for DURATION. run.py sets the environment and reads SUMMARY.
import http from 'k6/http';

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['p(50)', 'p(99)', 'avg', 'max'],
  scenarios: {
    clients: { executor: 'constant-vus', vus: Number(__ENV.CLIENTS), duration: __ENV.DURATION },
  },
};

export default function () {
  http.get(__ENV.URL, { timeout: '5s' });
}

export function handleSummary(data) {
  return { [__ENV.SUMMARY]: JSON.stringify(data) };
}
