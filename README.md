# Introduction
Let's say you have a kubernetes cluster running deployment A and deployment B and a service which is pointing to deployment A but you want it to failover to deployment B in case deployment A goes down. Whenever deployment A is back, you want to have the service switch back to that.

These priority-based failover scenarios can be implemented using this controller: it monitors its namespace for services with the label `failoverLabel` and a selector including this failover label. It will also check the namespace for any pods matching the selector defined by the service _excluding_ the failover label. It will then check the pods for a `failoverPriority` label (defaults to `0`). Finally, the pod(s) with the highest priority and status `Ready` will be patched to have the failover label with the value specified in the service selector and remove the failover label value from all other pods matching the service selector.

Additionally, you can specify the label `failoverMinReplicas` for your pods (if not specified it defaults to `1`). This prevents the service from being re-routed when less than the specified number of replicas are ready. The number of replicas are counted across all pods with the same value for the `pod-template-hash`. In case you prefer to count the number of live replicas across different pod templates, you can add a label `failoverGroup`; any pods with the same value are grouped together to determine the number of replicas.

# Environment variables

There are two environment variables which can be specified:

- `FAILOVER_CONTROLLER_LOG_LEVEL` can have the values `"DEBUG"`, `"INFO"` (default), `"WARNING"` or `"ERROR"``
- `FAILOVER_CONTROLLER_UPDATE_INTERVAL` expects a positive numeric value (float or integer) and will limit the number of requests to the kubernetes API server by checking the services and pods in the namespace less often (defaults is 0.1 s)

# Example

Please refer to the example provided in `example/example.yaml` and the README on [Docker Hub](https://hub.docker.com/repository/docker/alphanoob1337/failover-controller)

# Build instructions

Check out the repository using `git clone https://github.com/alphanoob1337/failover-controller.git`,
change directory into the repository using `cd failover-controller` and
run `docker build -t failover-controller:dev .`.