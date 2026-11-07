---
name: openshift-cli
description: Use when reading or changing an OpenShift or Kubernetes cluster with oc or kubectl - inspecting resources, events and logs, diffing a manifest against live, or applying a change.
---

# OpenShift CLI

Read before you write, and name the cluster on every call.

## Read

- Name the kubeconfig and namespace on every call: `oc --kubeconfig <file> -n <namespace> get <kind> <name>`.
  A shell default from another task points at the wrong cluster.
- Use the full resource name when two API groups share a short name:
  `applications.argoproj.io`, `machines.cluster.x-k8s.io`.
- Status before logs: `oc get <kind> <name> -o jsonpath='{.status.conditions}'`, then
  `oc describe`, then `oc get events --sort-by=.lastTimestamp`, then `oc logs --tail=100`.
- `oc auth can-i <verb> <resource> --as=<user>` answers a permission question without
  trying the action.
- `oc explain <kind>.spec.<field>` shows a field of the cluster's own schema version.

## Change

1. Find what manages the object first: an `argocd.argoproj.io/instance` label or
   `app.kubernetes.io/managed-by` means a controller reverts a hand edit. Change its source.
2. Diff before you apply: `oc diff -f <file>` (or `oc diff -k <dir>`). The diff must hold
   only your change.
3. Validate with the server: `oc apply -f <file> --dry-run=server`.
4. Apply, then read the object back and check its status.

## Never

- `--force --grace-period=0`, or removing finalizers to unstick a delete: the controller
  that owns the finalizer never cleans up. Find what it waits for.
- `oc delete` of a Node: the kubelet registers only at start, so the host stays outside
  the cluster.
- Printing a Secret's data. Check that a key exists with
  `oc get secret <name> -o jsonpath='{.data}' | jq 'keys'`.
