pipelineJob('pickipedia-enrich-torrents') {
    description('Enrich Release pages with BitTorrent and IPFS metadata. Calls delivery-kid for torrent generation, probes IPFS gateway for file size/type, writes metadata via Blue Railroad bot.')

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
                                sh 'set +x && cd /opt/blue-railroad-import && python3 -m blue_railroad_import.cli enrich-torrents --wiki-url "https://pickipedia.xyz" --username "$BLUERAILROAD_BOT_USERNAME" --password "$BLUERAILROAD_BOT_PASSWORD" --delivery-kid-api-key "$DELIVERY_KID_API_KEY" -v'
                            }
                        }
                        stage('Enrich IPFS metadata') {
                            steps {
                                sh 'set +x && cd /opt/blue-railroad-import && python3 -m blue_railroad_import.cli enrich-ipfs --wiki-url "https://pickipedia.xyz" --username "$BLUERAILROAD_BOT_USERNAME" --password "$BLUERAILROAD_BOT_PASSWORD" -v'
                            }
                        }
                    }

                    post {
                        failure {
                            echo "Enrichment failed - check logs above"
                        }
                        success {
                            echo "Enrichment completed successfully"
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
