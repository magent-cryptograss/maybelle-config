pipelineJob('pickipedia') {
    definition {
        cpsScm {
            scm {
                git {
                    remote {
                        url('https://github.com/cryptograss/pickipedia.git')
                        credentials('github-token')
                    }
                    branch('*/production')
                }
            }
            scriptPath('Jenkinsfile')
        }
    }
    triggers {
        // Poll every 5 minutes for changes
        cron('*/5 * * * *')
    }
}
