pipelineJob('pickipedia-rsync-status') {
    description('Status check for PickiPedia deployments to NFS')
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    stages {
                        stage('Check deploy marker') {
                            steps {
                                sh """
                                    MARKER_FILE="/var/jenkins_home/pickipedia_stage/.deploy-ready"

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
                                    DEPLOY_LOG="/var/log/pickipedia-deploy.log"

                                    echo ""
                                    echo "=== Recent Deploy Log ==="

                                    if [ -f "\\$DEPLOY_LOG" ]; then
                                        tail -20 "\\$DEPLOY_LOG"

                                        echo ""
                                        echo "=== Last Successful Deploy ==="
                                        LAST_SUCCESS=\\$(grep -i "deploy successful" "\\$DEPLOY_LOG" | tail -1)
                                        if [ -n "\\$LAST_SUCCESS" ]; then
                                            echo "\\$LAST_SUCCESS"

                                            # Check age of last successful deploy
                                            LAST_TIME=\\$(echo "\\$LAST_SUCCESS" | sed 's/: .*[Dd]eploy successful//')
                                            LAST_EPOCH=\\$(date -d "\\$LAST_TIME" +%s 2>/dev/null || echo 0)
                                            NOW_EPOCH=\\$(date +%s)
                                            AGE_MINUTES=\\$(( (NOW_EPOCH - LAST_EPOCH) / 60 ))

                                            echo "Age: \\$AGE_MINUTES minutes"

                                            if [ "\\$AGE_MINUTES" -gt 10 ]; then
                                                echo ""
                                                echo "ERROR: Last successful deploy was more than 10 minutes ago!"
                                                exit 1
                                            fi
                                        else
                                            echo "(no successful deploys in log)"
                                            echo "ERROR: No successful deploys found!"
                                            exit 1
                                        fi
                                    else
                                        echo "(no deploy log found)"
                                        echo "ERROR: Deploy log file missing!"
                                        exit 1
                                    fi
                                """
                            }
                        }

                        stage('Check staging build') {
                            steps {
                                sh """
                                    STAGE_DIR="/var/jenkins_home/pickipedia_stage"

                                    echo ""
                                    echo "=== PickiPedia Staging Status ==="

                                    if [ -d "\\$STAGE_DIR" ]; then
                                        # Check build-info.php for version info
                                        if [ -f "\\$STAGE_DIR/build-info.php" ]; then
                                            echo "Build info:"
                                            cat "\\$STAGE_DIR/build-info.php" | grep -E "(blockheight|build_number|commit|build_time)" | head -10
                                        fi

                                        # Show LocalSettings timestamp
                                        if [ -f "\\$STAGE_DIR/LocalSettings.php" ]; then
                                            LS_TIME=\\$(stat -c '%y' "\\$STAGE_DIR/LocalSettings.php" | cut -d. -f1)
                                            echo ""
                                            echo "LocalSettings.php last modified: \\$LS_TIME"
                                        fi

                                        # Count extensions
                                        if [ -d "\\$STAGE_DIR/extensions" ]; then
                                            EXT_COUNT=\\$(ls -1 "\\$STAGE_DIR/extensions" | wc -l)
                                            echo "Extensions installed: \\$EXT_COUNT"
                                        fi
                                    else
                                        echo "WARNING: Staging directory not found!"
                                    fi
                                """
                            }
                        }
                    }

                    post {
                        always {
                            echo "PickiPedia deploy status check complete"
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
