// k6 in bursts: nothing for EVERY - 1 seconds, then BURST requests within one second, again and again for
// SECONDS, over at most CONNECTIONS virtual users. run.py sets the environment and reads SUMMARY.
import http from 'k6/http';

const burst = Number(__ENV.BURST), every = Number(__ENV.EVERY), rounds = Math.floor(Number(__ENV.SECONDS) / every);
const stages = [];
for (let i = 0; i < rounds; i++) {
  stages.push({ target: 0, duration: (every - 1) + 's' }, { target: burst, duration: '0s' },
              { target: burst, duration: '1s' }, { target: 0, duration: '0s' });
}

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['p(50)', 'p(99)', 'avg', 'max'],
  scenarios: {
    burst: { executor: 'ramping-arrival-rate', startRate: 0, timeUnit: '1s', stages: stages,
             preAllocatedVUs: Number(__ENV.CONNECTIONS), maxVUs: Number(__ENV.CONNECTIONS) },
  },
};

export default function () {
  http.get(__ENV.URL, { timeout: '5s' });
}

export function handleSummary(data) {
  return { [__ENV.SUMMARY]: JSON.stringify(data) };
}
