SHELL=/usr/bin/bash
CRON_TZ=UTC
PROJECT_DIR=/home/ubuntu/justicio

20 07 * * * $PROJECT_DIR/doc/cron_etl_daily_public.sh >> $HOME/justicio-etl.log 2>&1
