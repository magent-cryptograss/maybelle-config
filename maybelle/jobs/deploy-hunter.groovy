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

                        stage('Install maybelle backup key on hunter') {
                            steps {
                                sh """
                                    # Ensure backupuser exists and install maybelle's backup public key
                                    ssh root@hunter.cryptograss.live '
                                        # Create backupuser if doesn't exist
                                        id backupuser || useradd -m -s /bin/bash backupuser

                                        # Create .ssh directory
                                        mkdir -p /home/backupuser/.ssh
                                        chmod 700 /home/backupuser/.ssh
                                        chown backupuser:backupuser /home/backupuser/.ssh
                                    '

                                    # Copy maybelle's backup public key to hunter
                                    scp /var/jenkins_home/.ssh/id_ed25519_backup.pub \\
                                        root@hunter.cryptograss.live:/tmp/maybelle_backup.pub

                                    ssh root@hunter.cryptograss.live '
                                        # Install the key
                                        cat /tmp/maybelle_backup.pub >> /home/backupuser/.ssh/authorized_keys
                                        chmod 600 /home/backupuser/.ssh/authorized_keys
                                        chown backupuser:backupuser /home/backupuser/.ssh/authorized_keys
                                        rm /tmp/maybelle_backup.pub
                                    '
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
