pipelineJob('deploy-hunter') {
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    stages {
                        stage('Deploy to hunter') {
                            steps {
                                sh """
                                    ssh root@hunter.cryptograss.live '
                                        cd /root/maybelle-config &&
                                        git pull origin main &&
                                        cd hunter &&
                                        ./deploy.sh --do-not-copy-database
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
