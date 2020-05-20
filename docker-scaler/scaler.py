#!/usr/bin/env python3

import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import string
import random

DEBUG = True # overwritten by --verbose flag
K8S_NAMESPACE = "agents"
K8S_JOB_MAX_AGE_S = 3600

####################################
########## PIPELINES API ###########
####################################
def get_jobs_in_pool(organization, pool_id, token, status = "unassigned"):
  # status = unassigned, inProgress, finished
  cmd = "curl --silent -u :{0} https://dev.azure.com/{1}/_apis/distributedtask/pools/{2}/jobrequests?api-version=5.1".format(token, organization, pool_id)
  debug("Fetching Azure Pipelines jobs", cmd)
  out = subprocess.check_output([cmd], shell=True)
  out = out.decode("ascii")
  obj = json.loads(out)
  debug("Response from Azure Pipelines", json.dumps(obj, separators=(',', ':')))
  debug("Filtering for status", status)
  def filter_status_unassigned(job):
    if "assignTime" not in job:
      return True
    return False
  def filter_status_inprogress(job):
    if "assignTime" in job and "finishTime" not in job:
      return True
    return False
  def filter_status_finished(job):
    if "finishTime" in job:
      return True
    return False
  switcher = {
    "unassigned": filter_status_unassigned,
    "inProgress": filter_status_inprogress,
    "finished": filter_status_finished,
    "all": lambda job : job
  }
  filter_func = switcher.get(status, lambda job : None) # for unknown status report no jobs
  jobs = [j for j in obj["value"] if filter_func(j)]
  debug("Filtered Jobs", jobs)
  return jobs


def cleanup_agent_pool(organization, pool_id, token):
  cmd = "az pipelines agent list --organization 'https://dev.azure.com/{0}' --pool-id {1} -o json --query \"[?status=='offline']\"".format(organization, pool_id)
  out = subprocess.check_output([cmd], shell=True)
  offline_agents = json.loads(out.decode("ascii").strip())
  print_found_resource("offline agents", [e.get("name") + ":" + str(e.get("id")) for e in offline_agents])
  for agent in offline_agents:
    if agent.get("name") == "dummy":
      print("Skipping 'dummy' build agent to make sure builds can be queued")
      continue
    cmd = "curl --silent -o /dev/null -w '%{{http_code}}' -X DELETE -u :{3} https://dev.azure.com/{0}/_apis/distributedtask/pools/{1}/agents/{2}?api-version=1.0".format(organization, pool_id, agent.get("id"), token)
    debug("Removing agent via curl", cmd)
    out = subprocess.check_output([cmd], shell=True)
    status_code = int(out.decode("ascii").strip())
    if status_code//100 != 2: # check for any 2XX code, default is 204
      print("ERROR: Removing agent via curl failed with status code {0}".format(status_code))
      exit(1)

####################################
############# K8s API ##############
####################################
def create_release(path_chart, name, token, shared_volume_account_name, shared_volume_account_key):
  debug("Creating new HELM release with name:", name)
  token64 = base64.encodebytes(token.encode("utf8")).decode("ascii").replace("\n", "")
  account_name64 = base64.encodebytes(shared_volume_account_name.encode("utf8")).decode("ascii").replace("\n", "")
  account_key64 = base64.encodebytes(shared_volume_account_key.encode("utf8")).decode("ascii").replace("\n", "")
  cmd = "helm upgrade --install --namespace {3} --set devops.token={2} --set agent.sharedVolume.accountname={4} --set agent.sharedVolume.accountkey={5} {1} {0}".format(path_chart, name, token64, K8S_NAMESPACE, account_name64, account_key64)
  debug("Command", cmd)
  return subprocess.check_output([cmd], shell=True)

def delete_release(name):
  debug("Deleting HELM release with name:", name)
  cmd = "helm uninstall --namespace {1} {0}".format(name, K8S_NAMESPACE)
  debug("Command", cmd)
  return subprocess.check_output([cmd], shell=True)

def get_k8s_jobs():
  cmd = "kubectl get jobs -n {0} -o json".format(K8S_NAMESPACE)
  debug("Getting jobs via kubectl", cmd)
  out = subprocess.check_output([cmd], shell=True)
  out = out.decode("ascii")
  obj = json.loads(out)
  debug("Response", json.dumps(obj, separators=(',', ':')))
  return obj.get("items")

def filter_expired_k8s_jobs(jobs, max_age_s=K8S_JOB_MAX_AGE_S):
  expired_jobs = []
  for job in jobs:
    if job.get("status").get("active") != 1:
      completion_time = job.get("status").get("completionTime")
      if completion_time == None:
        expired_jobs.append(job)
      else:
        completion_time_date = dt.datetime.strptime(completion_time, "%Y-%m-%dT%H:%M:%SZ")
        diff = dt.datetime.utcnow() - completion_time_date
        if diff.seconds > max_age_s:
          expired_jobs.append(job)
  return expired_jobs

####################################
############## HELPER ##############
####################################
def debug(caption, payload):
  if DEBUG == True:
    print("DEBUG: " + caption)
    print("\t" + str(payload))

def setup_parser():
  parser = argparse.ArgumentParser(description="Scale Azure Pipelines agent if necessary. Requires az, kubectl, helm logged in and connected to cluster.")

  parser.add_argument('action', help="Which action to run", choices=["info", "clean", "autoscale", "spawn"])
  required = parser.add_argument_group('required arguments')
  optional = parser.add_argument_group('optional arguments')
  required.add_argument("-o", "--organization", help="Azure DevOps organization (name e.g. myOrg)", required=True)
  required.add_argument("-p", "--project", help="Azure DevOps project (name or ID)", required=True)
  required.add_argument("-t", "--token", help="PAT used by the build agent to connect to agent pool", type=str, required=True)
  required.add_argument("--helm-chart", type=str, help="path to helm chart")
  required.add_argument("--pool-id", help="Azure DevOps pool ID to use", required=True)
  optional.add_argument("-v", "--verbose", help="show verbose output", action="store_true")
  optional.add_argument("-c", "--count", help="number of K8s jobs to 'spawn'", type=int, default=1)
  optional.add_argument("--shared-volume-account-name", type=str, help="azure file storage account credentials for shared volume mount", default="")
  optional.add_argument("--shared-volume-account-key", type=str, help="azure file storage account credentials for shared volume mount", default="")
  optional.add_argument("--purge", help="'clean' deletes all K8s jobs not only expired ones", action="store_true")
  
  return parser

def get_k8s_job_release_name(job):
  return job.get("metadata").get("labels").get("release-name")

def print_found_resource(label, elements):
  print("Found {1} {0} ({2})".format(label, len(elements), ", ".join(elements)))

def random_string(length=8):
  return "".join(random.choice(string.ascii_lowercase + string.digits) for x in range(length))

####################################
############### MAIN ###############
####################################
if __name__ == "__main__":
  parser = setup_parser()
  args = parser.parse_args()
  DEBUG = args.verbose
  if args.action != "info" and not args.helm_chart:
      parser.error("--helm-chart required for actions other than 'info'")
  debug("Found following arguments", args)

  # fetch info from Azure Pipelines
  if args.action in ["info", "autoscale"]:
    get_jobs_in_pool(args.organization, args.pool_id, args.token, "unassigned")
    get_jobs_in_pool(args.organization, args.pool_id, args.token, "inProgress")
    pending_builds = get_jobs_in_pool(args.organization, args.pool_id, args.token, "unassigned")
    print_found_resource("pending builds", [e.get("definition").get("name") + ":" + e.get("owner").get("name") for e in pending_builds])
    running_builds = get_jobs_in_pool(args.organization, args.pool_id, args.token, "inProgress")
    print_found_resource("running builds", [e.get("definition").get("name") + ":" + e.get("owner").get("name") for e in running_builds])

  # fetch k8s info
  if args.action in ["info", "clean", "autoscale"]:
    k8s_jobs = get_k8s_jobs()
    active_jobs = [get_k8s_job_release_name(e) for e in k8s_jobs if e.get("status").get("active") == 1]
    print_found_resource("active K8s jobs", active_jobs)
    inactive_jobs= [get_k8s_job_release_name(e) for e in k8s_jobs if e.get("status").get("active") != 1]
    print_found_resource("inactive K8s jobs", inactive_jobs)
    expired_jobs= [get_k8s_job_release_name(job) for job in filter_expired_k8s_jobs(k8s_jobs)]
    print_found_resource("expired K8s jobs", expired_jobs)

  if args.action == "clean":
    print("Running 'clean' action")
    releases_to_delete = expired_jobs
    if args.purge == True:
      print("Cleaning with --purge")
      releases_to_delete += active_jobs + inactive_jobs
    if len(releases_to_delete) > 0:
      print("Deleting K8s jobs: {0}".format(", ".join(releases_to_delete)))
      delete_release(" ".join(releases_to_delete))
      print("Checking K8s jobs again..")
      k8s_jobs = get_k8s_jobs()
      active_jobs = [get_k8s_job_release_name(e) for e in k8s_jobs if e.get("status").get("active") == 1]
      print_found_resource("active K8s jobs", active_jobs)
      inactive_jobs= [get_k8s_job_release_name(e) for e in k8s_jobs if e.get("status").get("active") != 1]
      print_found_resource("inactive K8s jobs", inactive_jobs)
      expired_jobs= [get_k8s_job_release_name(job) for job in filter_expired_k8s_jobs(k8s_jobs)]
      print_found_resource("expired K8s jobs", expired_jobs)
    else:
      print("Nothing to delete")
    print("Cleaning up agent pool")
    cleanup_agent_pool(args.organization, args.pool_id, args.token)

  elif args.action == "spawn":
    print("Running 'spawn' action")
    release_count = args.count
    release_names = [random_string() for x in range(release_count)]
    print("Spawning {0} new K8s jobs: {1}".format(release_count, ", ".join(release_names)))
    for name in release_names:
      create_release(args.filepath, name, args.token, args.shared_volume_account_name, args.shared_volume_account_key)

    k8s_jobs = get_k8s_jobs()
    active_jobs = [get_k8s_job_release_name(e) for e in k8s_jobs if e.get("status").get("active") == 1]
    print_found_resource("active K8s jobs", active_jobs)

  elif args.action == "autoscale":
    print("Running 'autoscale' action")
    surplus_builds = len(pending_builds) + len(running_builds) - len(active_jobs)
    if surplus_builds > 0:
      print("Found {0} more builds than active K8s jobs, spawning more..".format(surplus_builds))
      for x in range(surplus_builds):
        name = random_string()
        print("Spawning {0}..".format(name))
        create_release(args.filepath, name, args.token, args.shared_volume_account_name, args.shared_volume_account_key)
    else:
      print("..no need to scale")
