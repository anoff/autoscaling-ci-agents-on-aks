#!/bin/sh

if [ -z "$AZP_ORG" ] || [ -z "$AZP_TOKEN" ]; then
  echo 1>&2  "one or more variables are undefined"
  echo "Please provide the following environment variables: AZP_ORG, AZP_TOKEN"
  exit 1
fi

echo "Logging into az devops.."
echo $AZP_TOKEN | az devops login --organization https://dev.azure.com/$AZP_ORG
