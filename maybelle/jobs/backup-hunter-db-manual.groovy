pipelineJob('backup-hunter-db-manual') {
    description('Manually trigger an immediate database backup from hunter')
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    stages {
                        stage('Get current block height') {
                            steps {
                                script {
                                    // Fetch current Ethereum block height
                                    def blockHeight = sh(
                                        script: 'curl -s https://eth.blockscout.com/api/v2/stats | jq -r .total_blocks',
                                        returnStdout: true
                                    ).trim()
                                    env.BLOCK_HEIGHT = blockHeight
                                    echo "Current Ethereum block: ${blockHeight}"
                                }
                            }
                        }

                        stage('Trigger backup on hunter') {
                            steps {
                                sh """
                                    # SSH to hunter and create backup immediately
                                    ssh backupuser@hunter.cryptograss.live '
                                        BACKUP_DIR="/var/backups/magenta"
                                        BACKUP_FILE="magenta_memory_manual_${BLOCK_HEIGHT}.dump"

                                        echo "Creating manual backup at block ${BLOCK_HEIGHT}..."
                                        docker exec magenta-postgres pg_dump -U magent -Fc magenta_memory > "\$BACKUP_DIR/\$BACKUP_FILE"

                                        # Count messages
                                        MSG_COUNT=\$(docker exec magenta-postgres psql -U magent -d magenta_memory -t -c "SELECT COUNT(*) FROM conversations_message;" | tr -d " ")
                                        echo "Backup complete: \$MSG_COUNT messages"
                                        echo "\$BACKUP_FILE"
                                    '
                                """
                            }
                        }

                        stage('Pull backup to maybelle') {
                            steps {
                                sh """
                                    # Create backup directory on maybelle
                                    mkdir -p /var/jenkins_home/hunter-db-backups

                                    # Pull the manual backup
                                    scp backupuser@hunter.cryptograss.live:/var/backups/magenta/magenta_memory_manual_${BLOCK_HEIGHT}.dump \\
                                        /var/jenkins_home/hunter-db-backups/

                                    # List all backups
                                    echo "Available backups on maybelle:"
                                    ls -lh /var/jenkins_home/hunter-db-backups/*.dump
                                """
                            }
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }
}
