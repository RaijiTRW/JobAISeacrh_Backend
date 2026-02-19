// PM2 ecosystem config for JobAISeacrh Backend
// Zero-downtime deployment with cluster mode
module.exports = {
  apps: [{
    name: 'jobaisearch-backend',
    script: 'uvicorn',
    args: 'main:app --host 0.0.0.0 --port 8000',
    cwd: '/var/www/jobaisearch/backend/JobAISeacrh_Backend',
    interpreter: 'python3',
    // Cluster mode with 2 instances for zero-downtime reloads
    instances: 2,
    exec_mode: 'cluster',
    env: {
      PYTHONPATH: '/var/www/jobaisearch/backend/JobAISeacrh_Backend',
    },
    // Graceful shutdown settings
    kill_timeout: 15000,
    wait_ready: true,
    listen_timeout: 20000,
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    error_file: '/var/www/jobaisearch/backend/JobAISeacrh_Backend/logs/error.log',
    out_file: '/var/www/jobaisearch/backend/JobAISeacrh_Backend/logs/out.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss',
    merge_logs: true,
  }],
};
