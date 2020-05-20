FROM ubuntu:16.04

# see https://storage.googleapis.com/kubernetes-release/release/stable.txt for latest release
ENV KUBECTL_VERSION="v1.18.0"
# see https://github.com/helm/helm/releases
ENV HELM_VERSION="v3.1.2"


RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    jq \
    python3

# add kubectl and helm
RUN curl -o /usr/local/bin/kubectl https://storage.googleapis.com/kubernetes-release/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl && \
    chmod +x /usr/local/bin/kubectl && \
    curl -fsSL https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz | \
    tar zxvf - --strip 1 -C /usr/local/bin/ linux-amd64/helm

# add azure CLI
RUN curl -sL https://aka.ms/InstallAzureCLIDeb | bash && \
    az extension add --name azure-devops

WORKDIR /app

COPY ./helm-batchjob-agent ./helm-job/
COPY scaler.py login.sh ./
