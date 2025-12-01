pipelineJob('backup-memory-lane') {
    description('Daily status check for Memory Lane database backups (actual backup runs via host cron)')
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    stages {
                        stage('Check backup status') {
                            steps {
                                sh """
                                    BACKUP_DIR="/mnt/persist/magenta/backups"

                                    echo "=== Memory Lane Backup Status ==="
                                    echo ""

                                    # Check if backup directory exists and is accessible
                                    if [ ! -d "\\$BACKUP_DIR" ]; then
                                        echo "ERROR: Backup directory not found!"
                                        exit 1
                                    fi

                                    # Find most recent backup
                                    LATEST=\\$(ls -t "\\$BACKUP_DIR"/*.dump 2>/dev/null | head -1)
                                    if [ -z "\\$LATEST" ]; then
                                        echo "ERROR: No backup files found!"
                                        exit 1
                                    fi

                                    # Check age of most recent backup
                                    LATEST_AGE_HOURS=\\$(( (\\$(date +%s) - \\$(stat -c%Y "\\$LATEST")) / 3600 ))
                                    LATEST_SIZE=\\$(stat -c%s "\\$LATEST")
                                    LATEST_NAME=\\$(basename "\\$LATEST")

                                    echo "Latest backup: \\$LATEST_NAME"
                                    echo "Size: \\$(numfmt --to=iec \\$LATEST_SIZE)"
                                    echo "Age: \\$LATEST_AGE_HOURS hours"
                                    echo ""

                                    # Warn if backup is too old (> 3 hours for bi-hourly backups)
                                    if [ \\$LATEST_AGE_HOURS -gt 3 ]; then
                                        echo "WARNING: Latest backup is more than 3 hours old!"
                                        exit 1
                                    else
                                        echo "OK: Backup is recent"
                                    fi
                                """
                            }
                        }

                        stage('List recent backups') {
                            steps {
                                sh """
                                    BACKUP_DIR="/mnt/persist/magenta/backups"

                                    echo ""
                                    echo "=== Recent Backups ==="
                                    ls -lht "\\$BACKUP_DIR"/*.dump 2>/dev/null | head -10

                                    echo ""
                                    BACKUP_COUNT=\\$(ls -1 "\\$BACKUP_DIR"/*.dump 2>/dev/null | wc -l)
                                    echo "Total backups on disk: \\$BACKUP_COUNT"
                                """
                            }
                        }

                        stage('Show backup log') {
                            steps {
                                sh """
                                    BACKUP_DIR="/mnt/persist/magenta/backups"

                                    echo ""
                                    echo "=== Recent Backup Log Entries ==="
                                    if [ -f "\\$BACKUP_DIR/backup.log" ]; then
                                        tail -10 "\\$BACKUP_DIR/backup.log"
                                    else
                                        echo "(no backup log found)"
                                    fi
                                """
                            }
                        }

                        stage('Check offsite backup status') {
                            steps {
                                sh """
                                    DAILY_DIR="/mnt/persist/magenta/backups/daily"
                                    OFFSITE_LOG="/var/log/nfs-backup.log"

                                    echo ""
                                    echo "=== Offsite Backup Status ==="

                                    # Check daily backup directory
                                    if [ ! -d "\\$DAILY_DIR" ]; then
                                        echo "WARNING: Daily backup directory not found"
                                    else
                                        DAILY_COUNT=\\$(ls -1 "\\$DAILY_DIR"/*.dump 2>/dev/null | wc -l)
                                        echo "Daily backups on disk: \\$DAILY_COUNT"

                                        if [ "\\$DAILY_COUNT" -gt 0 ]; then
                                            LATEST_DAILY=\\$(ls -t "\\$DAILY_DIR"/*.dump 2>/dev/null | head -1)
                                            LATEST_DAILY_NAME=\\$(basename "\\$LATEST_DAILY")
                                            LATEST_DAILY_AGE_DAYS=\\$(( (\\$(date +%s) - \\$(stat -c%Y "\\$LATEST_DAILY")) / 86400 ))
                                            echo "Latest daily: \\$LATEST_DAILY_NAME (\\$LATEST_DAILY_AGE_DAYS days old)"
                                        fi
                                    fi

                                    echo ""
                                    echo "=== Offsite Sync Log ==="
                                    if [ -f "\\$OFFSITE_LOG" ]; then
                                        tail -5 "\\$OFFSITE_LOG"

                                        # Check if last sync was successful and recent
                                        LAST_SUCCESS=\\$(grep "successful" "\\$OFFSITE_LOG" | tail -1)
                                        if [ -n "\\$LAST_SUCCESS" ]; then
                                            echo ""
                                            echo "Last successful sync: \\$LAST_SUCCESS"
                                        fi

                                        # Fail if no successful sync in last 36 hours
                                        LAST_SYNC_TIME=\\$(grep "Starting offsite" "\\$OFFSITE_LOG" | tail -1 | cut -d: -f1-3)
                                        if [ -n "\\$LAST_SYNC_TIME" ]; then
                                            # Parse the date and check age
                                            LAST_SYNC_EPOCH=\\$(date -d "\\$LAST_SYNC_TIME" +%s 2>/dev/null || echo 0)
                                            NOW_EPOCH=\\$(date +%s)
                                            SYNC_AGE_HOURS=\\$(( (NOW_EPOCH - LAST_SYNC_EPOCH) / 3600 ))
                                            if [ "\\$SYNC_AGE_HOURS" -gt 36 ]; then
                                                echo "WARNING: Last offsite sync attempt was \\$SYNC_AGE_HOURS hours ago!"
                                            fi
                                        fi
                                    else
                                        echo "(no offsite sync log found - sync may not have run yet)"
                                    fi
                                """
                            }
                        }
                    }

                    post {
                        failure {
                            echo "Backup status check failed - investigate immediately!"
                        }
                        success {
                            echo "Backup status check passed"
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }
    triggers {
        cron('30 */2 * * *')  // Run every 2 hours at :30 (after bi-hourly backup at :00)
    }
}
