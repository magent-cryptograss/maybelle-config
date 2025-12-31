pipelineJob('pickipedia-rsync-status') {
    description('Status check for PickiPedia deployments to NFS')
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    environment {
                        DEPLOY_STALE = 'false'
                        STALE_MINUTES = '0'
                    }

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

                                        # Check how old the marker is
                                        MARKER_AGE=\\$(( (\\$(date +%s) - \\$(stat -c %Y "\\$MARKER_FILE")) / 60 ))
                                        echo "Marker age: \\$MARKER_AGE minutes"
                                        if [ "\\$MARKER_AGE" -gt 5 ]; then
                                            echo "WARNING: Marker has been waiting more than 5 minutes - cron may not be running"
                                        fi
                                    else
                                        echo "No pending deploy (marker not present)"
                                    fi
                                """
                            }
                        }

                        stage('Check deploy status') {
                            steps {
                                script {
                                    def result = sh(script: """
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
                                                    echo "STALE: Last successful deploy was \\$AGE_MINUTES minutes ago"
                                                    echo "\\$AGE_MINUTES" > /tmp/pickipedia_stale_minutes
                                                    exit 1
                                                fi
                                            else
                                                echo "(no successful deploys in log)"
                                                echo "STALE: No successful deploys found"
                                                echo "9999" > /tmp/pickipedia_stale_minutes
                                                exit 1
                                            fi
                                        else
                                            echo "(no deploy log found)"
                                            echo "STALE: Deploy log file missing"
                                            echo "9999" > /tmp/pickipedia_stale_minutes
                                            exit 1
                                        fi
                                    """, returnStatus: true)

                                    if (result != 0) {
                                        env.DEPLOY_STALE = 'true'
                                        env.STALE_MINUTES = sh(script: 'cat /tmp/pickipedia_stale_minutes 2>/dev/null || echo 0', returnStdout: true).trim()
                                    }
                                }
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

                                        # Check if Sentry is in vendor
                                        if [ -d "\\$STAGE_DIR/vendor/sentry" ]; then
                                            echo "Sentry SDK: installed"
                                        else
                                            echo "Sentry SDK: NOT installed"
                                        fi
                                    else
                                        echo "WARNING: Staging directory not found!"
                                    fi
                                """
                            }
                        }

                        stage('Investigate stale deploy') {
                            when {
                                expression { env.DEPLOY_STALE == 'true' }
                            }
                            steps {
                                sh """
                                    echo ""
                                    echo "=========================================="
                                    echo "=== INVESTIGATING STALE DEPLOY ==="
                                    echo "=========================================="
                                    echo "Deploy is \\${STALE_MINUTES} minutes stale"
                                    echo ""

                                    # Check if deploys are PAUSED
                                    echo "=== Deploy Pause Status ==="
                                    PAUSE_FILE="/var/jenkins_home/.pickipedia-deploy-paused"
                                    if [ -f "\\$PAUSE_FILE" ]; then
                                        echo "*** DEPLOYS ARE PAUSED ***"
                                        echo "Pause file: \\$PAUSE_FILE"
                                        echo "Reason: \\$(cat \\$PAUSE_FILE)"
                                        echo ""
                                        echo "To resume deploys, remove this file:"
                                        echo "  rm \\$PAUSE_FILE"
                                    else
                                        echo "Deploys are NOT paused (no pause file)"
                                    fi
                                    echo ""

                                    # Check marker file status
                                    echo "=== Deploy Marker Status ==="
                                    MARKER_FILE="/var/jenkins_home/pickipedia_stage/.deploy-ready"
                                    if [ -f "\\$MARKER_FILE" ]; then
                                        echo "Marker EXISTS - waiting to be deployed"
                                        MARKER_AGE=\\$(( (\\$(date +%s) - \\$(stat -c %Y "\\$MARKER_FILE")) / 60 ))
                                        echo "Marker age: \\$MARKER_AGE minutes"
                                        echo "Contents: \\$(cat \\$MARKER_FILE)"
                                        if [ "\\$MARKER_AGE" -gt 5 ]; then
                                            echo ""
                                            echo "*** MARKER IS STALE - cron may not be running or rsync is failing ***"
                                        fi
                                    else
                                        echo "No marker file - either:"
                                        echo "  1. Cron picked it up but rsync failed (check log for errors)"
                                        echo "  2. Build isn't creating marker (check pickipedia-build job)"
                                    fi
                                    echo ""

                                    # Check for deploy script
                                    echo "=== Deploy Script ==="
                                    DEPLOY_SCRIPT="/usr/local/bin/deploy-pickipedia-to-nfs.sh"
                                    if [ -f "\\$DEPLOY_SCRIPT" ]; then
                                        echo "Deploy script exists: \\$DEPLOY_SCRIPT"
                                        ls -la "\\$DEPLOY_SCRIPT"
                                    else
                                        echo "*** DEPLOY SCRIPT MISSING ***"
                                        echo "Expected: \\$DEPLOY_SCRIPT"
                                        echo "This script is created by Ansible - run maybelle playbook to fix"
                                    fi
                                    echo ""

                                    # Note: SSH/rsync runs as root cron on HOST, not inside this container
                                    # So we can't directly test SSH from here, but we can check the log for clues
                                    echo "=== Note ==="
                                    echo "The deploy cron runs as root on the HOST (not in Jenkins container)"
                                    echo "SSH issues would appear in the deploy log above"
                                    echo ""

                                    # Check the full deploy log for recent activity
                                    echo "=== Last 30 Lines of Deploy Log ==="
                                    DEPLOY_LOG="/var/log/pickipedia-deploy.log"
                                    if [ -f "\\$DEPLOY_LOG" ]; then
                                        tail -30 "\\$DEPLOY_LOG"
                                        echo ""
                                        echo "=== Recent Errors in Log ==="
                                        grep -iE "error|fail|denied|refused|timeout|paused" "\\$DEPLOY_LOG" | tail -10 || echo "(no errors found)"
                                    else
                                        echo "*** DEPLOY LOG MISSING: \\$DEPLOY_LOG ***"
                                    fi
                                    echo ""

                                    echo "=== Investigation Complete ==="
                                """
                            }
                        }

                        stage('Final Status') {
                            steps {
                                script {
                                    if (env.DEPLOY_STALE == 'true') {
                                        error("Deploy is stale (${env.STALE_MINUTES} minutes since last successful deploy)")
                                    } else {
                                        echo "Deploy status: HEALTHY"
                                    }
                                }
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
