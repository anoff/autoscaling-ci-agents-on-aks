# Autoscaling Azure DevOps Agents running on AKS

## Repo structure

- `docker-agent`: a Dockerfile to build a DevOps agent image, taken from [Azure docs](https://docs.microsoft.com/en-us/azure/devops/pipelines/agents/docker?view=azure-devops#create-and-build-the-dockerfile-1) for 
- `docker-scaler`: the sourcecode for the container that will do the scaling i.e. check if there is need for a new agent and trigger a pod deployment within AKS
- `helm-batchjob-agent`: helm chart that is used to deploy a new agent, configure your agents (memory, CPU, disks) in this chart
- `helm-cronjob-scaler`: the _master_ chart that needs to be deployed once to start the autoscaler within your cluster, here you configure your Azure DevOps organization + secrets

## How to setup autoscaling DevOps agents in AKS

1. Create Kubernetes environment for build agents
1. Prepare DevOps project for this agent pool
1. Deploy autoscaler script as CRON job to cluster

## step 1: create Azure Kubernetes Service (AKS) cluster for the build agents

e.g. following the official [Azure docs to _Deploy an Azure Kubernetes Service cluster using the Azure CLI_](https://docs.microsoft.com/en-us/azure/aks/kubernetes-walkthrough)

After creating the cluster it needs to get access permissions to the docker registry (ACR) so the agent images can be downloaded

```sh
#!/bin/bash

# grant AKS service principal access to docker registry
#   details see https://docs.microsoft.com/en-us/azure/container-registry/container-registry-auth-aks
AKS_RESOURCE_GROUP=myClusterResourceGroup
AKS_CLUSTER_NAME=myClusterName
ACR_RESOURCE_GROUP=myContainerResourceGroup
ACR_NAME=myACRname

# Get the id of the service principal configured for AKS
CLIENT_ID=$(az aks show --resource-group $AKS_RESOURCE_GROUP --name $AKS_CLUSTER_NAME --query "servicePrincipalProfile.clientId" --output tsv)

# Get the ACR registry resource id
ACR_ID=$(az acr show --name $ACR_NAME --resource-group $ACR_RESOURCE_GROUP --query "id" --output tsv)

# Create role assignment
az role assignment create --assignee $CLIENT_ID --role acrpull --scope $ACR_ID
```

## step 2: prepare DevOps project for agent pool

1. Prerequisite: Install the Azure CLI
    1. get the base `az` CLI: `curl -sL https://aka.ms/InstallAzureCLIDeb | sudo -E bash` (WARNING: this is the yolo-version where you trust that the shell script is sane, do not run this in production instead follow the [manual setup instructions](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli-apt?view=azure-cli-latest#manual-install-instructions))
    1. install the devops extension `az extension add --name azure-devops`
    1. optional: configure the defaults `az devops configure --defaults organization=https://dev.azure.com/<my devops org>/ project="<my devops project>"`
1. Create an Agent Pool
    1. Open Project -> Settings -> Agent Pools
    1. add a new pool, give it a meaningful name as it will be used within the pipelines e.g. `ubuntu18-aks`
      🚨 pools without agents can not be assigned any jobs (will fail immediately), therefore we need at least one dummy agent available in the agent pool so we can queue jobs
    1. Create a _dummy_ agent (named `dummy`) by following the [instructions to create a dedicated agent](https://docs.microsoft.com/en-us/azure/devops/pipelines/agents/v2-linux?view=azure-devops#download-and-configure-the-agent)
    1. Stop the agent and feel free to remove any local files, after it has been registered once it is no longer needed - DO NOT REMOVE OR DISABLE THE DUMMY AGENT

## step 3: Deploy autoscaler script

Required tools:
- Helm
- kubectl
- az (Azure CLI)

1. Make sure the `pipelines-scaler` and `pipelines-agent` docker images are available in the container registry
    1. for Dockerfiles see `docker-scaler` and `docker-agent`
1. Activate kubectl for the correct AKS cluster e.g. `az aks get-credentials -g <resource group> -n <aks cluster name>`
1. Create the namespace for agent pods to run in `kubectl create namespace agents`, this needs to match the definition in `docker-scaler/scaler.py`
1. Put a valid Personal Access Token in your environment variables `AZP_TOKEN` with the **Agent Pools: Read & Manage** permission
1. Within this repository execute the following commands to deploy the Helm chart
    ```sh
    cd helm-cronjob-scaler
    helm upgrade --install \
      --set devops.token=`echo ${AZP_TOKEN} | base64` \
      scaler .
    ```
1. Done! Agents will now automatically be created if a job is pending

## Local development / Debugging

### Running the scaler manually

Preparations

* place a `.env` file with `AZP_TOKEN=<PAT token>` into the `docker-scaler/` directory containing a PAT token that has **Agent Pools: Read & Manage** permission

Run the commands

```sh
cd docker-scaler/
source .env # to load the AZP_TOKEN variable
./scaler.py -o <devops org> -p "<devops project>" -t $AZP_TOKEN --helm-chart helm-batchjob-agent --pool-name <pipelines pool name> info

### Azure Kubernetes

Debugging Kubernetes

```sh
# deploy the dashboard
kubectl apply -f https://raw.githubusercontent.com/kubernetes/dashboard/v2.0.0-beta8/aio/deploy/recommended.yaml

# HINT FOR WSL
#   the azure CLI will automatically open the (very long) URL automatically, for this to work WSL needs to know which browser to open
export BROWSER='/c/Program Files (x86)/Mozilla Firefox/firefox.exe'

# browse the dashboard
az aks browse -g myClusterResourceGroup -n myClusterName
```

Deploying AKS based agents manually

```sh
helm upgrade --install --namespace agents --set devops.token=`echo ${AZP_TOKEN} | base64` rick ./helm-batchjob-agent
```
