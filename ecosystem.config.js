const home = process.env.HOME;
const repo = `${home}/plutus-agent`;

module.exports = {
  apps: [
    {
      name: "plutus-gateway",
      script: `${home}/.local/bin/plutus`,
      args: "gateway run",
      cwd: repo,
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      kill_timeout: 10000,
    },
    {
      name: "plutus-watchers",
      script: `${repo}/.venv/bin/python`,
      args: "-m watchers.run",
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
