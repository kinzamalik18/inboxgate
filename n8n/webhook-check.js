// Watches the inbound webhook for as long as n8n runs.
//
//   node webhook-check.js <webhook url> <n8n pid>
//
// n8n unpublishes a workflow when activating it fails, for example after a
// database timeout during startup or a leadership change, and the webhook then
// answers 404 until someone republishes it. This exits 1 when the webhook goes
// missing, so start.sh can stop n8n and let the host restart it cleanly (the
// next start republishes the workflow). It exits 0 if n8n stops on its own.
//
// The probe is a CORS preflight: n8n answers 204 when the webhook is
// registered and an error when it isn't, without running the workflow.

const [url, pidArg] = process.argv.slice(2);
const n8nPid = Number(pidArg);
// n8n opens its port minutes before it finishes activating workflows on a
// small instance, so startup gets a long allowance before a 404 counts.
const STARTUP_LIMIT_MS = 10 * 60 * 1000;
const CHECK_EVERY_MS = 60 * 1000;
const FAILURES_BEFORE_RESTART = 3;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function n8nRunning() {
  try {
    process.kill(n8nPid, 0);
    return true;
  } catch {
    return false;
  }
}

async function registered() {
  try {
    const response = await fetch(url, {
      method: "OPTIONS",
      headers: { Origin: "http://localhost", "Access-Control-Request-Method": "POST" },
      signal: AbortSignal.timeout(10000),
    });
    return response.status < 400 ? "yes" : "no";
  } catch {
    return "unreachable";
  }
}

async function waitForStartup() {
  const deadline = Date.now() + STARTUP_LIMIT_MS;
  while (Date.now() < deadline && n8nRunning()) {
    if ((await registered()) === "yes") return true;
    await sleep(5000);
  }
  return !n8nRunning() ? null : false;
}

async function main() {
  const started = await waitForStartup();
  if (started === null) return 0;
  if (!started) {
    console.log("Webhook check: not registered after startup.");
    return 1;
  }
  console.log("Webhook check: registered. Watching it every minute.");

  let failures = 0;
  while (n8nRunning()) {
    await sleep(CHECK_EVERY_MS);
    if (!n8nRunning()) break;
    if ((await registered()) === "yes") {
      failures = 0;
    } else if (++failures >= FAILURES_BEFORE_RESTART) {
      console.log("Webhook check: webhook went missing.");
      return 1;
    }
  }
  return 0;
}

main().then((code) => process.exit(code));
