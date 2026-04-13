pipelineJob('pickipedia-import-bluerailroad') {
    description('Import Blue Railroad token data into PickiPedia wiki pages. Runs every even minute (after chain data fetch on odd minutes).')

    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    options {
                        disableConcurrentBuilds()
                        buildDiscarder(logRotator(numToKeepStr: '30'))
                        timeout(time: 5, unit: 'MINUTES')
                    }

                    environment {
                        CHAIN_DATA = '/var/jenkins_home/shared/chain_data/chainData.json'
                        WIKI_URL = 'https://pickipedia.xyz'
                    }

                    stages {
                        stage('Check chain data') {
                            steps {
                                script {
                                    if (!fileExists(env.CHAIN_DATA)) {
                                        echo "Chain data not found at ${env.CHAIN_DATA}"
                                        currentBuild.result = 'NOT_BUILT'
                                        return
                                    }
                                    echo "Chain data found: ${env.CHAIN_DATA}"
                                }
                            }
                        }

                        stage('Run import') {
                            steps {
                                sh 'set +x && /opt/blue-railroad-import/bin/python -m blue_railroad_import.cli import --chain-data "$CHAIN_DATA" --wiki-url "$WIKI_URL" --username "$BLUERAILROAD_BOT_USERNAME" --password "$BLUERAILROAD_BOT_PASSWORD" -v'
                            }
                        }
                    }

                    post {
                        failure {
                            echo "Blue Railroad import failed - check logs above"
                        }
                        success {
                            echo "Blue Railroad import completed successfully"
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }

    triggers {
        cron('*/2 * * * *')  // Run every even minute (0, 2, 4, ...)
    }
}
