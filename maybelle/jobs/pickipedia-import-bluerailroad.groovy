pipelineJob('pickipedia-import-bluerailroad') {
    description('Import Blue Railroad token data into PickiPedia wiki pages. Runs automatically after pickipedia deploys via host cron. Check /var/log/pickipedia-deploy.log for import status.')

    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    options {
                        buildDiscarder(logRotator(numToKeepStr: '30'))
                    }

                    stages {
                        stage('Check import status') {
                            steps {
                                script {
                                    echo "=== Blue Railroad Import Status ==="
                                    echo ""
                                    echo "The import runs automatically from the HOST deploy cron after each"
                                    echo "successful PickiPedia deployment. It cannot run from Jenkins directly"
                                    echo "because the SSH keys are on the host, not in the container."
                                    echo ""
                                    echo "To check import status, review the deploy log:"

                                    sh """
                                        echo "=== Recent Import Activity ==="
                                        grep -i "blue railroad" /var/log/pickipedia-deploy.log 2>/dev/null | tail -10 || echo "(no import entries found)"

                                        echo ""
                                        echo "=== Last Successful Deploy ==="
                                        grep "deploy successful" /var/log/pickipedia-deploy.log 2>/dev/null | tail -3 || echo "(no deploys found)"
                                    """

                                    echo ""
                                    echo "To manually run the import, SSH to maybelle host and run:"
                                    echo "  ssh -i /root/.ssh/id_ed25519_nfs jmyles_pickipedia@ssh.nyc1.nearlyfreespeech.net"
                                    echo "      cd ~/public && php extensions/BlueRailroadIntegration/maintenance/importBlueRailroads.php"
                                }
                            }
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }

    // No automatic trigger - runs from host deploy cron after successful pickipedia deploy
}
