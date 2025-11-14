pipelineJob('deploy-hunter') {
    description('Hunter deployment job - triggered externally via deploy-hunter-remote.sh')

    // Disable this job from being run directly in Jenkins UI
    disabled(true)

    definition {
        cps {
            script('''
                pipeline {
                    agent any
                    stages {
                        stage('Info') {
                            steps {
                                echo "This job is triggered externally via deploy-hunter-remote.sh"
                                echo "Logs and history are recorded via Jenkins API"
                                echo "Do not run this job directly - use the deployment script instead"
                            }
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }
}
