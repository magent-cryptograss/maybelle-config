FROM jenkins/jenkins:lts-jdk17

USER root

# Install Node.js dependencies and other required packages
RUN apt-get update && apt-get install -y \
    build-essential \
    libcairo2-dev \
    libpango1.0-dev \
    libjpeg-dev \
    libgif-dev \
    librsvg2-dev \
    rsync \
    sudo \
    curl \
    jq \
    docker.io \
    php-cli \
    php-xml \
    php-mbstring \
    php-intl \
    php-curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Composer
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

USER jenkins

# Install required plugins (mcp-server installed separately from custom build)
RUN jenkins-plugin-cli --plugins \
    configuration-as-code \
    workflow-aggregator \
    git \
    github \
    credentials-binding \
    pipeline-utility-steps \
    matrix-auth \
    job-dsl \
    workflow-job \
    pipeline-model-definition \
    pipeline-stage-view \
    pipeline-graph-view \
    dark-theme \
    github-branch-source \
    mask-passwords \
    build-name-setter \
    pipeline-github

# Install custom mcp-server plugin with reduced response sizes
# See: https://github.com/jenkinsci/mcp-server-plugin/pull/113
# Downloaded from magent-cryptograss fork until PR is merged upstream
ADD --chown=jenkins:jenkins https://github.com/magent-cryptograss/mcp-server-plugin/releases/download/v0.87-reduced-context/mcp-server.hpi /usr/share/jenkins/ref/plugins/mcp-server.hpi

# Create directory for Configuration as Code
RUN mkdir -p /var/jenkins_home/casc_configs

# Skip initial setup wizard
ENV JAVA_OPTS -Djenkins.install.runSetupWizard=false