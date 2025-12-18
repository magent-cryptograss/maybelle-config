pipelineJob('backup-pickipedia') {
    description('Daily backup of PickiPedia MySQL database from NFS, synced to hunter for preview environments')

    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    options {
                        buildDiscarder(logRotator(numToKeepStr: '30'))
                        timeout(time: 10, unit: 'MINUTES')
                    }

                    stages {
                        stage('Backup') {
                            steps {
                                sh '/usr/local/bin/backup-pickipedia.sh'
                            }
                        }
                    }

                    post {
                        success {
                            echo "Backup completed successfully"
                        }
                        failure {
                            echo "Backup failed - check /var/log/pickipedia-backup.log on maybelle"
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }

    triggers {
        cron('30 3 * * *')  // Daily at 3:30 AM
    }
}
