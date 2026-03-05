pipelineJob('delivery-kid-uptime') {
    description('Health check for delivery-kid pinning service and IPFS gateway - runs every 5 minutes. Alerts after 2 consecutive failures.')
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    options {
                        disableConcurrentBuilds()
                    }

                    environment {
                        FAILURE_COUNT_FILE = '/var/jenkins_home/delivery-kid-uptime-failures.txt'
                        ALERT_THRESHOLD = '2'
                        ALERT_EMAILS = 'justin@cryptograss.live,sky@cryptograss.live,rj@cryptograss.live'
                    }

                    stages {
                        stage('Run health checks') {
                            steps {
                                script {
                                    // Use the Python test script for comprehensive checks
                                    def result = sh(
                                        script: '/mnt/persist/maybelle-config/delivery-kid/scripts/test-delivery-kid.py --json',
                                        returnStdout: true
                                    ).trim()

                                    def json = readJSON text: result
                                    echo "Checks passed: ${json.passed}/${json.total}"

                                    // Show individual check results
                                    json.checks.each { check ->
                                        def status = check.passed ? '✓' : '✗'
                                        def time = check.response_time_ms ? " (${check.response_time_ms.toInteger()}ms)" : ''
                                        echo "${status} ${check.name}: ${check.message}${time}"
                                    }

                                    if (!json.all_passed) {
                                        def failed = json.checks.findAll { !it.passed }.collect { it.name }
                                        error("FAIL: Checks failed: ${failed.join(', ')}")
                                    }

                                    echo "All health checks passed"
                                }
                            }
                        }
                    }

                    post {
                        failure {
                            script {
                                echo "=== DELIVERY-KID IS DOWN ==="

                                // Read current failure count
                                def failureCount = 1
                                if (fileExists(env.FAILURE_COUNT_FILE)) {
                                    def countStr = readFile(env.FAILURE_COUNT_FILE).trim()
                                    failureCount = countStr.isInteger() ? countStr.toInteger() + 1 : 1
                                }

                                // Write updated count
                                writeFile file: env.FAILURE_COUNT_FILE, text: failureCount.toString()
                                echo "Consecutive failures: ${failureCount}"

                                // Check if we should alert
                                if (failureCount == env.ALERT_THRESHOLD.toInteger()) {
                                    echo "=== ALERT THRESHOLD REACHED ==="
                                    echo "delivery-kid has been down for ${failureCount} consecutive checks."
                                    echo "Alert emails would go to: ${env.ALERT_EMAILS}"
                                    echo "Email sending not yet configured - see GitHub issue #35"
                                } else if (failureCount > env.ALERT_THRESHOLD.toInteger()) {
                                    echo "Still down (${failureCount} failures). Alert already sent at threshold."
                                }
                            }
                        }
                        success {
                            script {
                                echo "delivery-kid health check passed"

                                // Check if we were previously down and should send recovery alert
                                if (fileExists(env.FAILURE_COUNT_FILE)) {
                                    def countStr = readFile(env.FAILURE_COUNT_FILE).trim()
                                    def prevFailures = countStr.isInteger() ? countStr.toInteger() : 0

                                    if (prevFailures >= env.ALERT_THRESHOLD.toInteger()) {
                                        echo "=== RECOVERY ==="
                                        echo "delivery-kid is back UP after ${prevFailures} failures"
                                    }

                                    // Reset failure count
                                    sh 'rm -f /var/jenkins_home/delivery-kid-uptime-failures.txt'
                                }
                            }
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }
    triggers {
        cron('*/5 * * * *')  // Run every 5 minutes
    }
}
