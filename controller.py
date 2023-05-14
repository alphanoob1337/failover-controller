import os
import sys
import time
import datetime
import kubernetes
from enum import Enum

# Helper functions
class LogLevels(Enum):
    DEBUG = 1
    INFO = 2
    WARNING = 3
    ERROR = 4

def log(level, message):
    if level.value >= log_level.value:
        print(datetime.datetime.now().strftime('[%Y-%m-%d %H:%M:%S.%f]'), '['+level.name+']', message, file=sys.stderr)

def match_pod_to_selector(pod, selector):
    if pod.metadata.labels is None:
        return False
    
    for slabel, svalue in selector.items():
        if slabel in pod.metadata.labels:
            if svalue is None or svalue == pod.metadata.labels[slabel]:
                return True
            else:
                return False
        else:
            return False

# Global settings
log_level = LogLevels.INFO
if 'FAILOVER_CONTROLLER_LOG_LEVEL' in os.environ:
    log_level_name = os.environ['FAILOVER_CONTROLLER_LOG_LEVEL']
    if log_level_name in LogLevels.__members__:
        log_level = LogLevels[log_level_name]
    else:
        log(LogLevels.WARNING, 'FAILOVER_CONTROLLER_LOG_LEVEL provided but not "DEBUG", "INFO", "WARNING" or "ERROR"! Proceeding with default setting "INFO".')

try:
    update_interval = abs(float(os.environ['FAILOVER_CONTROLLER_UPDATE_INTERVAL'])) if 'FAILOVER_CONTROLLER_UPDATE_INTERVAL' in os.environ else .1
except ValueError:
    log(LogLevels.WARNING, 'FAILOVER_CONTROLLER_UPDATE_INTERVAL provided but cannot be converted to a numeric value! Proceeding with default value of 100 ms.')
    update_interval = .1

kubernetes.config.load_incluster_config()

# Determine namespace
namespace = 'default'
namespace_file = '/var/run/secrets/kubernetes.io/serviceaccount/namespace'
if os.path.isfile(namespace_file):
    with open(namespace_file, 'r') as ifh:
        namespace = ifh.read()

# Initiate client
v1 = kubernetes.client.CoreV1Api()

# Main loop
while True:
    start_time = time.time()
    
    # Discover services and pods
    services = v1.list_namespaced_service(namespace, watch=False)
    pods = v1.list_namespaced_pod(namespace, watch=False)

    for service in services.items:
        if 'failoverLabel' in service.metadata.labels and service.metadata.labels['failoverLabel'] is not None:
            failover_label = service.metadata.labels['failoverLabel']
            if service.spec.selector is None or failover_label not in service.spec.selector or service.spec.selector[failover_label] is None:
                log(LogLevels.ERROR, 'Service '+service.metadata.name+' can not be used for automatic priority-based failover since the label "'+failover_label+'" is missing in the selector specification (or no value is provided)!')
            else:
                svc_selector = service.spec.selector
                failover_status_name = svc_selector[failover_label]
                log(LogLevels.DEBUG, 'Service '+service.metadata.name+' found with selector '+str({k: v for k, v in svc_selector.items() if k != failover_label})+'.')

                # Discover all potential endpoints for the service
                svc_endpoints = {}
                template_hash_map = {}
                for pod in pods.items:
                    if match_pod_to_selector(pod, svc_selector):

                        # Derive endpoint ids
                        template_hash = None
                        if 'pod-template-hash' in pod.metadata.labels and pod.metadata.labels['pod-template-hash'] is not None:
                            template_hash = pod.metadata.labels['pod-template-hash']
                        
                        if 'failoverGroup' in pod.metadata.labels and pod.metadata.labels['failoverGroup'] is not None:
                            endpoint_id = ('group', pod.metadata.labels['failoverGroup'])
                        elif template_hash is not None:
                            endpoint_id = ('template-hash', template_hash)
                            if template_hash not in template_hash_map:
                                template_hash_map[template_hash] = endpoint_id
                        else:
                            endpoint_id = ('name', pod.metadata.name) # If no failover group nor template-hash provided, use the pod name
                        
                        # Get associated configuration
                        priority = 0
                        if 'failoverPriority' in pod.metadata.labels:
                            try:
                                priority_ = int(pod.metadata.labels['failoverPriority'])
                                if priority_ < 0:
                                    raise ValueError
                                else:
                                    priority = priority_
                            except ValueError:
                                log(LogLevels.ERROR, 'Invalid failoverPriority label encountered in pod '+pod.metadata.name+'. Positive integer value expected!')
                        
                        min_replica = 1
                        if 'failoverMinReplicas' in pod.metadata.labels:
                            try:
                                min_replica_ = int(pod.metadata.labels['failoverMinReplicas'])
                                if min_replica_ < 1:
                                    raise ValueError
                                else:
                                    min_replica = min_replica_
                            except ValueError:
                                log(LogLevels.ERROR, 'Invalid failoverMinReplicas label encountered in pod '+pod.metadata.name+'. Integer value larger than 1 expected!')

                        # Add to list of potential endpoints
                        if endpoint_id not in svc_endpoints:
                            svc_endpoints[endpoint_id] = { 'priority': priority, 'min_replica': min_replica, 'pods': [pod] }
                        else:
                            if priority > svc_endpoints[endpoint_id]['priority']:
                                svc_endpoints[endpoint_id]['priority'] = priority
                            if min_replica < svc_endpoints[endpoint_id]['min_replica']:
                                svc_endpoints[endpoint_id]['min_replica'] = min_replica
                            
                            svc_endpoints[endpoint_id]['pods'] += [pod]
                
                # Check if enough replicas are ready
                for eid, endpoint in svc_endpoints.items():
                    num_pods_ready = 0
                    for pod in endpoint['pods']:
                        if pod.status is not None and pod.status.container_statuses is not None:
                            if all([(state.started and state.ready) for state in pod.status.container_statuses]):
                                num_pods_ready += 1
                    
                    svc_endpoints[eid]['allowed'] = num_pods_ready >= endpoint['min_replica']

                # Find highest allowed priority in list of potential endpoints
                sorted_prios = sorted(set([endpoint['priority'] for endpoint in svc_endpoints.values() if endpoint['allowed']]), reverse=True)
                if len(sorted_prios) > 0:
                    active_prio = sorted_prios[0]
                else:
                    active_prio = None

                # Apply labels accordingly
                for eid, endpoint in svc_endpoints.items():
                    if active_prio is not None and endpoint['allowed'] and endpoint['priority'] == active_prio:
                        for pod in endpoint['pods']:
                            if failover_label not in pod.metadata.labels or pod.metadata.labels[failover_label] is None or failover_status_name not in pod.metadata.labels[failover_label]:
                                log(LogLevels.INFO, 'Attaching label "'+failover_label+'" with status "'+failover_status_name+'" to '+pod.metadata.name+'.')
                                desired_labels = pod.metadata.labels
                                desired_labels[failover_label] = failover_status_name
                                v1.patch_namespaced_pod(pod.metadata.name, pod.metadata.namespace, { 'metadata': { 'labels': desired_labels } })
                    else:
                        for pod in endpoint['pods']:
                            if failover_label in pod.metadata.labels and pod.metadata.labels[failover_label] is not None and failover_status_name in pod.metadata.labels[failover_label]:
                                log(LogLevels.INFO, 'Removing label "'+failover_status_name+'" from '+pod.metadata.name+'.')
                                desired_labels = pod.metadata.labels
                                desired_labels[failover_label] = None
                                v1.patch_namespaced_pod(pod.metadata.name, pod.metadata.namespace, { 'metadata': { 'labels': desired_labels } })
    
    sleep_time = start_time + update_interval - time.time()
    if sleep_time > 0.:
        time.sleep(sleep_time)
    else:
        log(LogLevels.INFO, 'Update interval violated.')
    
    log(LogLevels.DEBUG, '')