pipelineJob('deploy-status') {
    description('Status check for production deployments to NearlyFreeSpeech')
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    stages {
                        stage('Check deploy marker') {
                            steps {
                                sh """
                                    MARKER_FILE="/var/jenkins_home/www/builds/production/.deploy-ready"

                                    echo "=== Deploy Marker Status ==="
                                    echo ""

                                    if [ -f "\\$MARKER_FILE" ]; then
                                        echo "PENDING DEPLOY found:"
                                        cat "\\$MARKER_FILE"
                                        echo ""
                                        echo "Waiting for root cron to pick up (runs every 2 min)"
                                    else
                                        echo "No pending deploy (marker not present)"
                                    fi
                                """
                            }
                        }

                        stage('Show deploy log') {
                            steps {
                                sh """
                                    DEPLOY_LOG="/var/log/nfs-deploy.log"

                                    echo ""
                                    echo "=== Recent Deploy Log ==="

                                    if [ -f "\\$DEPLOY_LOG" ]; then
                                        tail -20 "\\$DEPLOY_LOG"

                                        echo ""
                                        echo "=== Last Successful Deploy ==="
                                        LAST_SUCCESS=\\$(grep "Deploy successful" "\\$DEPLOY_LOG" | tail -1)
                                        if [ -n "\\$LAST_SUCCESS" ]; then
                                            echo "\\$LAST_SUCCESS"
                                        else
                                            echo "(no successful deploys in log)"
                                        fi
                                    else
                                        echo "(no deploy log found)"
                                    fi
                                """
                            }
                        }

                        stage('Check production build') {
                            steps {
                                sh """
                                    BUILD_DIR="/var/jenkins_home/www/builds/production"

                                    echo ""
                                    echo "=== Production Build Status ==="

                                    if [ -d "\\$BUILD_DIR" ]; then
                                        # Count files
                                        FILE_COUNT=\\$(find "\\$BUILD_DIR" -type f | wc -l)
                                        echo "Files in production build: \\$FILE_COUNT"

                                        # Show newest files
                                        echo ""
                                        echo "Most recently modified files:"
                                        find "\\$BUILD_DIR" -type f -printf '%T+ %p\\n' 2>/dev/null | sort -r | head -5

                                        # Check index.html timestamp
                                        if [ -f "\\$BUILD_DIR/justinholmes.com/index.html" ]; then
                                            JH_TIME=\\$(stat -c '%y' "\\$BUILD_DIR/justinholmes.com/index.html" | cut -d. -f1)
                                            echo ""
                                            echo "justinholmes.com/index.html last modified: \\$JH_TIME"
                                        fi

                                        if [ -f "\\$BUILD_DIR/cryptograss.live/index.html" ]; then
                                            CG_TIME=\\$(stat -c '%y' "\\$BUILD_DIR/cryptograss.live/index.html" | cut -d. -f1)
                                            echo "cryptograss.live/index.html last modified: \\$CG_TIME"
                                        fi
                                    else
                                        echo "WARNING: Production build directory not found!"
                                    fi
                                """
                            }
                        }
                    }

                    post {
                        always {
                            echo "Deploy status check complete"
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
