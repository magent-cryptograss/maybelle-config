pipelineJob('deploy-hunter') {
    parameters {
        choiceParam('DB_BACKUP', ['none', 'latest', 'select'], 'Database backup to restore')
        stringParam('BACKUP_FILE', '', 'Specific backup file (if select chosen)')
    }
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    stages {
                        stage('List available backups') {
                            when {
                                expression { params.DB_BACKUP != 'none' }
                            }
                            steps {
                                sh """
                                    echo "Available database backups:"
                                    ls -lh /var/jenkins_home/hunter-db-backups/*.dump || echo "No backups found"
                                """
                            }
                        }

                        stage('Copy backup to hunter') {
                            when {
                                expression { params.DB_BACKUP != 'none' }
                            }
                            steps {
                                sh """
                                    if [ "${params.DB_BACKUP}" = "latest" ]; then
                                        BACKUP="/var/jenkins_home/hunter-db-backups/latest.dump"
                                    else
                                        BACKUP="/var/jenkins_home/hunter-db-backups/${params.BACKUP_FILE}"
                                    fi

                                    # Copy backup to hunter
                                    scp "\$BACKUP" root@hunter.cryptograss.live:/tmp/restore_db.dump
                                """
                            }
                        }

                        stage('Deploy to hunter') {
                            steps {
                                sh """
                                    ssh root@hunter.cryptograss.live '
                                        cd /root/maybelle-config &&
                                        git fetch origin &&
                                        git checkout production &&
                                        git pull origin production &&
                                        cd hunter &&
                                        if [ "${params.DB_BACKUP}" != "none" ]; then
                                            ./deploy.sh -e db_dump_file=/tmp/restore_db.dump
                                        else
                                            ./deploy.sh --do-not-copy-database
                                        fi
                                    '
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
