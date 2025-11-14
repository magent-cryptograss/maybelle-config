pipelineJob('backup-hunter-db') {
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    stages {
                        stage('Pull database backup from hunter') {
                            steps {
                                script {
                                    // Get current block height
                                    def blockHeight = sh(
                                        script: 'curl -s https://eth.blockscout.com/api/v2/stats | jq -r .total_blocks',
                                        returnStdout: true
                                    ).trim()
                                    env.BLOCK_HEIGHT = blockHeight
                                }
                                sh """
                                    # Create backup directory on maybelle
                                    mkdir -p /var/jenkins_home/hunter-db-backups

                                    # Pull latest backup from hunter (using non-privileged backupuser)
                                    scp backupuser@hunter.cryptograss.live:/var/backups/magenta/latest.dump \\
                                        /var/jenkins_home/hunter-db-backups/magenta_auto_${BLOCK_HEIGHT}.dump

                                    # Create latest symlink
                                    ln -sf magenta_auto_${BLOCK_HEIGHT}.dump \\
                                        /var/jenkins_home/hunter-db-backups/latest.dump

                                    # Keep only last 30 days
                                    find /var/jenkins_home/hunter-db-backups -name "magenta_*.dump" -mtime +30 -delete

                                    # List available backups
                                    echo "Available backups:"
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
    triggers {
        cron('0 3 * * *')  // Run daily at 3am (1 hour after hunter creates backup)
    }
}
