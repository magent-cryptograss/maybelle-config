pipelineJob('backup-pickipedia') {
    description('Status check for PickiPedia MySQL backups (actual backup runs via host cron)')

    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    options {
                        buildDiscarder(logRotator(numToKeepStr: '30'))
                    }

                    stages {
                        stage('Check backup log') {
                            steps {
                                sh """
                                    BACKUP_LOG="/var/log/pickipedia-backup.log"

                                    echo "=== PickiPedia Backup Status ==="
                                    echo ""

                                    if [ -f "\\$BACKUP_LOG" ]; then
                                        echo "Recent log entries:"
                                        tail -20 "\\$BACKUP_LOG"

                                        echo ""
                                        echo "=== Last Successful Backup ==="
                                        LAST_SUCCESS=\\$(grep "Backup successful" "\\$BACKUP_LOG" | tail -1)
                                        if [ -n "\\$LAST_SUCCESS" ]; then
                                            echo "\\$LAST_SUCCESS"

                                            # Check age - warn if older than 25 hours
                                            # Extract datetime from log line (first 19 chars: YYYY-MM-DD HH:MM:SS)
                                            LAST_DATE=\\$(echo "\\$LAST_SUCCESS" | cut -c1-19)
                                            if [ -n "\\$LAST_DATE" ]; then
                                                LAST_EPOCH=\\$(date -d "\\$LAST_DATE" +%s 2>/dev/null || echo 0)
                                                NOW_EPOCH=\\$(date +%s)
                                                AGE_HOURS=\\$(( (NOW_EPOCH - LAST_EPOCH) / 3600 ))

                                                echo "Age: \\$AGE_HOURS hours"

                                                if [ "\\$AGE_HOURS" -gt 25 ]; then
                                                    echo ""
                                                    echo "ERROR: Last successful backup was more than 25 hours ago!"
                                                    exit 1
                                                fi
                                            fi
                                        else
                                            echo "(no successful backups in log)"
                                            echo "WARNING: No successful backups found yet"
                                        fi
                                    else
                                        echo "(no backup log found - backup may not have run yet)"
                                    fi
                                """
                            }
                        }

                        stage('Check backup files') {
                            steps {
                                sh """
                                    BACKUP_DIR="/mnt/persist/pickipedia/backups"

                                    echo ""
                                    echo "=== Backup Files ==="

                                    if [ -d "\\$BACKUP_DIR" ]; then
                                        ls -lh "\\$BACKUP_DIR"/*.sql.gz 2>/dev/null || echo "(no backup files found)"

                                        echo ""
                                        LATEST=\\$(ls -t "\\$BACKUP_DIR"/*.sql.gz 2>/dev/null | head -1)
                                        if [ -n "\\$LATEST" ]; then
                                            SIZE=\\$(stat -c%s "\\$LATEST")
                                            echo "Latest: \\$LATEST (\\$SIZE bytes)"

                                            if [ "\\$SIZE" -lt 1000 ]; then
                                                echo "ERROR: Backup file suspiciously small!"
                                                exit 1
                                            fi
                                        fi
                                    else
                                        echo "WARNING: Backup directory not found"
                                    fi
                                """
                            }
                        }
                    }

                    post {
                        always {
                            echo "Backup status check complete"
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }

    triggers {
        cron('0 4 * * *')  // Check daily at 4:00 AM (after 3:30 backup)
    }
}
