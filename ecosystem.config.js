const home = process.env.HOME;
const repo = `${home}/plutus-agent`;

module.exports = {
  apps: [
    {
      name: "plutus-gateway",
      script: `${repo}/.venv/bin/plutus`,
      args: "gateway run",
      cwd: repo,
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      kill_timeout: 10000,
      max_memory_restart: "900M",
      error_file: `${home}/.plutus-agent/logs/gateway-error.log`,
      out_file: `${home}/.plutus-agent/logs/gateway-out.log`,
    },
    {
      name: "plutus-watchers",
      script: `${repo}/.venv/bin/python`,
      args: "-m harness.watchers.run",
      cwd: repo,
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      kill_timeout: 5000,
      error_file: `${home}/.plutus-agent/logs/watchers-error.log`,
      out_file: `${home}/.plutus-agent/logs/watchers-out.log`,
    },
  ],
};
