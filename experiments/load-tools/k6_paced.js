// k6 "paced": the same total rate as k6_http.js, but spread evenly over all connections, like wrk2. One scenario per
// connection, each with exactly one virtual user (one keep-alive connection) sending RATE/CONNECTIONS requests per
// second on its own timetable. k6_http.js instead lets whichever user is free send the next request, so at a low
// latency a few connections carry almost everything. run.py sets the environment and reads SUMMARY.
import http from 'k6/http';

const conns = Number(__ENV.CONNECTIONS);
const scenarios = {};
for (let i = 0; i < conns; i++) {
  scenarios['c' + i] = {
    executor: 'constant-arrival-rate',
    rate: Number(__ENV.RATE) / conns,
    timeUnit: '1s',
    duration: __ENV.DURATION,
    preAllocatedVUs: 1,
    maxVUs: 1,
  };
}

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['p(50)', 'p(99)', 'avg', 'max'],
  scenarios,
};

export default function () {
  http.get(__ENV.URL, { timeout: '5s' });
}

export function handleSummary(data) {
  return { [__ENV.SUMMARY]: JSON.stringify(data) };
}
