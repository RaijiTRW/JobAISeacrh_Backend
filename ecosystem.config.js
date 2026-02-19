// PM2 ecosystem config for JobAISeacrh Backend
// Single instance with graceful reload for zero-downtime deployment
module.exports = {
  apps: [{
    name: 'jobaisearch-backend',
    script: '/var/www/jobaisearch/backend/JobAISeacrh_Backend/venv/bin/python',
    args: '-m uvicorn main:app --host 0.0.0.0 --port 8000',
    cwd: '/var/www/jobaisearch/backend/JobAISeacrh_Backend',
    instances: 1,
    exec_mode: 'fork',
    env: {
      PYTHONPATH: '/var/www/jobaisearch/backend/JobAISeacrh_Backend',
    },
    kill_timeout: 30000,
    wait_ready: true,
    listen_timeout: 30000,
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    error_file: '/var/www/jobaisearch/backend/JobAISeacrh_Backend/logs/error.log',
    out_file: '/var/www/jobaisearch/backend/JobAISeacrh_Backend/logs/out.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss',
    merge_logs: true,
  }],
};
