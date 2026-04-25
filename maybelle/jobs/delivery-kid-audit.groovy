pipelineJob('delivery-kid-audit') {
    description('Run delivery-kid storage audit and post result to Cryptograss:delivery-kid-audits/<blockheight>. Manually triggered; also runs daily at 04:00 UTC.')

    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    options {
                        disableConcurrentBuilds()
                        buildDiscarder(logRotator(numToKeepStr: '30'))
                        timeout(time: 15, unit: 'MINUTES')
                    }

                    environment {
                        WIKI_URL = 'https://pickipedia.xyz'
                    }

                    stages {
                        stage('Run audit and post to wiki') {
                            steps {
                                sh 'set +x && /opt/blue-railroad-import/bin/python /mnt/persist/maybelle-config/maybelle/scripts/post-audit-to-wiki.py'
                            }
                        }
                    }

                    post {
                        failure {
                            echo "Audit run failed — check console output above."
                        }
                        success {
                            echo "Audit posted. See Cryptograss:delivery-kid-audits on PickiPedia."
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }

    triggers {
        cron('0 4 * * *')
    }
}
