pipelineJob('pickipedia-uptime') {
    description('HTTP health check for PickiPedia production (pickipedia.xyz) - runs every minute')
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    stages {
                        stage('Check HTTP status') {
                            steps {
                                script {
                                    def httpCode = sh(script: """
                                        curl -s -o /dev/null -w "%{http_code}" --max-time 30 "https://pickipedia.xyz/wiki/Main_Page"
                                    """, returnStdout: true).trim()

                                    echo "HTTP Status: ${httpCode}"

                                    if (httpCode == '200') {
                                        echo "OK: PickiPedia is UP"
                                    } else {
                                        error("FAIL: PickiPedia returned HTTP ${httpCode}")
                                    }
                                }
                            }
                        }

                        stage('Check API endpoint') {
                            steps {
                                script {
                                    def apiCode = sh(script: """
                                        curl -s -o /dev/null -w "%{http_code}" --max-time 30 "https://pickipedia.xyz/api.php?action=query&meta=siteinfo&format=json"
                                    """, returnStdout: true).trim()

                                    echo "API Status: ${apiCode}"

                                    if (apiCode == '200') {
                                        echo "OK: MediaWiki API responding"
                                    } else {
                                        echo "WARNING: API returned HTTP ${apiCode}"
                                    }
                                }
                            }
                        }
                    }

                    post {
                        failure {
                            echo "=== PICKIPEDIA IS DOWN ==="
                            echo "Check NFS error logs and .htaccess configuration"
                        }
                        success {
                            echo "PickiPedia health check passed"
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }
    triggers {
        cron('* * * * *')  // Run every minute
    }
}
