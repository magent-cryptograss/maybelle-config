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
