pipelineJob('pickipedia-enrich-torrents') {
    description('Enrich Release pages with BitTorrent metadata. Calls delivery-kid for torrent generation, writes metadata via Blue Railroad bot.')

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

                    stages {
                        stage('Enrich torrents') {
                            steps {
                                sh 'set +x && /opt/blue-railroad-import/bin/python -m blue_railroad_import.cli enrich-torrents --wiki-url "https://pickipedia.xyz" --username "$BLUERAILROAD_BOT_USERNAME" --password "$BLUERAILROAD_BOT_PASSWORD" --delivery-kid-api-key "$DELIVERY_KID_API_KEY" -v'
                            }
                        }
                    }

                    post {
                        failure {
                            echo "Torrent enrichment failed - check logs above"
                        }
                        success {
                            echo "Torrent enrichment completed successfully"
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }

    triggers {
        cron('*/2 * * * *')  // Run every 2 minutes
    }
}
