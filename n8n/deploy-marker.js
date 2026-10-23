// Remembers which version of the workflow file is already imported and published,
// so a restart can skip the slow CLI setup when nothing changed.
//
//   node deploy-marker.js check <hash>   exit 0 if <hash> is recorded and the workflow is live
//   node deploy-marker.js mark <hash>    record <hash> after a successful import and publish
//
// Any error (no database, unexpected table layout) exits non-zero, which makes
// start.sh fall back to running the full setup.

const { Client } = require(require.resolve("pg", { paths: ["/usr/local/lib/node_modules/n8n"] }));

const WORKFLOW_ID = "InboxGateFlow001";
const [mode, hash] = process.argv.slice(2);
const env = process.env;
const schema = (env.DB_POSTGRESDB_SCHEMA || "public").replace(/[^a-zA-Z0-9_]/g, "");
const table = `"${schema}".inboxgate_deploy`;

async function main() {
  if (!hash || !["check", "mark"].includes(mode)) throw new Error("usage: check|mark <hash>");
  const client = new Client({
    host: env.DB_POSTGRESDB_HOST,
    port: Number(env.DB_POSTGRESDB_PORT || 5432),
    database: env.DB_POSTGRESDB_DATABASE,
    user: env.DB_POSTGRESDB_USER,
    password: env.DB_POSTGRESDB_PASSWORD,
    ssl: env.DB_POSTGRESDB_SSL_ENABLED === "true"
      ? { rejectUnauthorized: env.DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED !== "false" }
      : undefined,
    connectionTimeoutMillis: 15000,
  });
  await client.connect();
  try {
    await client.query(`create table if not exists ${table} (key text primary key, value text not null)`);
    if (mode === "mark") {
      await client.query(
        `insert into ${table} (key, value) values ('workflow_hash', $1)
         on conflict (key) do update set value = excluded.value`,
        [hash],
      );
      return 0;
    }
    const marker = await client.query(`select value from ${table} where key = 'workflow_hash'`);
    const live = await client.query(`select active from "${schema}".workflow_entity where id = $1`, [WORKFLOW_ID]);
    return marker.rows[0]?.value === hash && live.rows[0]?.active === true ? 0 : 1;
  } finally {
    await client.end();
  }
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error(`deploy-marker: ${err.message}`);
    process.exit(2);
  },
);
